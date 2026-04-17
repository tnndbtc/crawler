"""
migrate_to_postgres — copy all data from SQLite to PostgreSQL.

Usage:
    python manage.py migrate_to_postgres

Requires:
    - DATABASE_URL env var pointing to the target PostgreSQL database
    - db.sqlite3 present at the project root (source)
    - manage.py migrate already run against PostgreSQL (schema must exist)

What it does:
    1. Opens a second Django DB connection to the SQLite source
    2. Disables FK constraints in PostgreSQL for the duration
    3. Truncates every target table (RESTART IDENTITY CASCADE)
    4. Copies every row in batches of BATCH_SIZE
    5. Re-enables FK constraints
    6. Resets all PostgreSQL sequences to match the migrated data

Tables skipped:
    django_migrations  — PostgreSQL already has correct migration history
    django_session     — stale sessions are irrelevant after migration
"""

import gc
import time
from io import StringIO

from django.apps import apps
from django.conf import settings
from django.core.management import BaseCommand, call_command
from django.db import connections


BATCH_SIZE = 500

SKIP_TABLES = {
    'django_migrations',
    'django_session',
}


class Command(BaseCommand):
    help = 'Migrate all data from SQLite to the configured PostgreSQL database'

    def handle(self, *args, **options):
        self._check_preconditions()
        self._add_sqlite_connection()

        conn_pg = connections['default']
        conn_sq = connections['sqlite']

        # ── Phase 1: disable FK constraints ──────────────────────────────────
        self.stdout.write('Disabling FK constraints in PostgreSQL...')
        with conn_pg.cursor() as cur:
            cur.execute("SET session_replication_role = replica")

        # ── Phase 2: truncate all target tables ───────────────────────────────
        self.stdout.write('Truncating PostgreSQL tables...')
        all_models = list(apps.get_models(include_auto_created=True))
        with conn_pg.cursor() as cur:
            for model in all_models:
                table = model._meta.db_table
                if table in SKIP_TABLES:
                    continue
                try:
                    cur.execute(
                        f'TRUNCATE TABLE "{table}" RESTART IDENTITY CASCADE'
                    )
                except Exception as e:
                    self.stdout.write(
                        self.style.WARNING(f'  TRUNCATE {table}: {e}')
                    )

        # ── Phase 3: copy data ─────────────────────────────────────────────
        self.stdout.write('\nMigrating tables:')
        start = time.time()
        total_rows = 0
        errors = []

        for model in all_models:
            try:
                rows = self._migrate_table(model)
                total_rows += rows
            except Exception as e:
                table = model._meta.db_table
                self.stdout.write(self.style.ERROR(f'  ✗ {table}: {e}'))
                errors.append((table, str(e)))

        # ── Phase 4: re-enable FK constraints ────────────────────────────────
        self.stdout.write('\nRe-enabling FK constraints...')
        with conn_pg.cursor() as cur:
            cur.execute("SET session_replication_role = DEFAULT")

        # ── Phase 5: reset sequences ──────────────────────────────────────────
        self.stdout.write('Resetting PostgreSQL sequences...')
        buf = StringIO()
        app_labels = [
            cfg.label for cfg in apps.get_app_configs()
            if cfg.label not in ('staticfiles', 'messages')
        ]
        try:
            call_command(
                'sqlsequencereset',
                *app_labels,
                database='default',
                stdout=buf,
                no_color=True,
            )
            sql = buf.getvalue().strip()
            if sql:
                with conn_pg.cursor() as cur:
                    cur.execute(sql)
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'  Sequence reset warning: {e}'))

        elapsed = time.time() - start
        self.stdout.write('')
        self.stdout.write('=' * 60)
        self.stdout.write(
            self.style.SUCCESS(
                f'Migration complete: {total_rows:,} rows in {elapsed:.1f}s'
            )
        )
        if errors:
            self.stdout.write(self.style.ERROR(f'\nErrors ({len(errors)}):'))
            for table, err in errors:
                self.stdout.write(f'  {table}: {err}')
        self.stdout.write('=' * 60)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _check_preconditions(self):
        import os
        from pathlib import Path

        db_url = os.environ.get('DATABASE_URL', '')
        if not db_url or not db_url.startswith('postgres'):
            self.stderr.write(
                'ERROR: DATABASE_URL is not set or does not point to PostgreSQL.\n'
                'Set DATABASE_URL=postgres://... in .env and re-run.'
            )
            raise SystemExit(1)

        sqlite_path = Path(settings.BASE_DIR) / 'db.sqlite3'
        if not sqlite_path.exists():
            self.stderr.write(f'ERROR: SQLite source not found at {sqlite_path}')
            raise SystemExit(1)

        self.stdout.write(f'Source: {sqlite_path}')
        # Hide password in URL for display
        safe_url = db_url.split('@')[-1] if '@' in db_url else db_url
        self.stdout.write(f'Target: {safe_url}')
        self.stdout.write('')

    def _add_sqlite_connection(self):
        """
        Register a second Django DB connection pointing at the SQLite source.

        We add the entry to settings.DATABASES after django.setup() has run,
        so Django's ensure_defaults() hasn't been called on it yet.  Supply
        every key that ensure_defaults() would set so there are no KeyErrors
        when the connection is first used.
        """
        from pathlib import Path
        sqlite_path = str(Path(settings.BASE_DIR) / 'db.sqlite3')
        settings.DATABASES['sqlite'] = {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': sqlite_path,
            # Keys that Django's ensure_defaults() normally injects:
            'TIME_ZONE': None,
            'OPTIONS': {},
            'CONN_MAX_AGE': 0,
            'CONN_HEALTH_CHECKS': False,
            'AUTOCOMMIT': True,
            'ATOMIC_REQUESTS': False,
            'USER': '',
            'PASSWORD': '',
            'HOST': '',
            'PORT': '',
            'TEST': {'NAME': None},
        }
        # Touch the connection to trigger Django's internal setup for this alias.
        _ = connections['sqlite']

    def _migrate_table(self, model) -> int:
        table = model._meta.db_table

        if table in SKIP_TABLES:
            self.stdout.write(f'  SKIP  {table}')
            return 0

        total = model.objects.using('sqlite').count()
        if total == 0:
            self.stdout.write(f'  EMPTY {table}')
            return 0

        self.stdout.write(f'  {table}: {total} rows... ', ending='')
        self.stdout.flush()

        pk = model._meta.pk
        use_pk_cursor = (
            pk is not None and
            pk.__class__.__name__ in ('AutoField', 'BigAutoField')
        )

        migrated = 0

        if use_pk_cursor:
            # Efficient: paginate by PK — no expensive OFFSET scan
            last_pk = 0
            while True:
                batch = list(
                    model.objects.using('sqlite')
                    .filter(**{f'{pk.name}__gt': last_pk})
                    .order_by(pk.name)[:BATCH_SIZE]
                )
                if not batch:
                    break
                self._write_batch(model, batch)
                migrated += len(batch)
                last_pk = getattr(batch[-1], pk.name)
                gc.collect()
        else:
            # Fallback: offset-based for composite / non-integer PKs
            offset = 0
            while True:
                batch = list(
                    model.objects.using('sqlite')[offset:offset + BATCH_SIZE]
                )
                if not batch:
                    break
                self._write_batch(model, batch)
                migrated += len(batch)
                offset += len(batch)
                gc.collect()

        self.stdout.write(self.style.SUCCESS(f'✓ {migrated}'))
        return migrated

    @staticmethod
    def _write_batch(model, batch):
        """Mark objects as new inserts targeting 'default' and bulk_create."""
        for obj in batch:
            obj._state.db = 'default'
            obj._state.adding = True
        model.objects.using('default').bulk_create(
            batch,
            ignore_conflicts=False,
        )
