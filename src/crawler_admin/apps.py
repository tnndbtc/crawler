"""
Django app configuration for crawler_admin.
"""

from django.apps import AppConfig


def _configure_sqlite(sender, connection, **kwargs):
    """
    Set SQLite PRAGMAs on every new DB connection.

    Called via the connection_created signal so every process — API server,
    workers, manage.py shell — picks up the same settings.

    WAL + NORMAL synchronous: safe for a single-host setup and much faster
    than WAL + FULL (the Django default) because NORMAL only fsyncs at
    checkpoints, not on every commit.

    wal_autocheckpoint=500: checkpoint WAL at ~2 MB instead of the default
    4 MB. Smaller WAL = faster reads for concurrent readers (option 7, API).

    busy_timeout=30000: 30-second write-lock wait instead of the 5-second
    default. Prevents "database is locked" errors when multiple workers
    write concurrently.

    cache_size=-32000: 32 MB page cache per connection. Reduces physical
    reads on the 1 GB database.

    mmap_size=536870912: 512 MB memory-mapped I/O. Reduces pread() syscall
    overhead for sequential scans.
    """
    if connection.vendor != 'sqlite':
        return
    cursor = connection.cursor()
    cursor.execute('PRAGMA journal_mode=WAL')
    cursor.execute('PRAGMA synchronous=NORMAL')
    cursor.execute('PRAGMA wal_autocheckpoint=500')
    cursor.execute('PRAGMA busy_timeout=30000')
    cursor.execute('PRAGMA cache_size=-32000')
    cursor.execute('PRAGMA temp_store=MEMORY')
    cursor.execute('PRAGMA mmap_size=536870912')


class CrawlerAdminConfig(AppConfig):
    """Configuration for the crawler_admin Django app."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'crawler_admin'
    verbose_name = 'Trend Crawler Administration'

    def ready(self):
        """Connect audit signals when the app is ready."""
        from crawler_admin.signals import connect_signals
        connect_signals()

        # Apply SQLite PRAGMAs on every new connection to keep WAL small and
        # reads fast under concurrent multi-process worker load.
        from django.db.backends.signals import connection_created
        connection_created.connect(_configure_sqlite)
