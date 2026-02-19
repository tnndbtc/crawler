"""
Django management command to view domain health report.

Displays observability data for HTTP requests per domain:
- Circuit breaker status
- Rate limiting status
- Request counts and error rates
- Recent metrics
- Backoff multipliers

Usage:
    python manage.py domain_report
    python manage.py domain_report --last-24h
    python manage.py domain_report --domain news.ycombinator.com
    python manage.py domain_report --show-all
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from crawler_admin.models import DomainPolicy, DomainMetrics, URLCache


class Command(BaseCommand):
    help = 'Display domain health and metrics report'

    def add_arguments(self, parser):
        parser.add_argument(
            '--domain',
            type=str,
            help='Show report for specific domain only',
        )
        parser.add_argument(
            '--last-24h',
            action='store_true',
            help='Show metrics from last 24 hours only',
        )
        parser.add_argument(
            '--show-all',
            action='store_true',
            help='Show all domains (including those with no activity)',
        )

    def handle(self, *args, **options):
        domain_filter = options.get('domain')
        last_24h = options.get('last_24h')
        show_all = options.get('show_all')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('═' * 70))
        self.stdout.write(self.style.SUCCESS('  Domain Health Report'))
        self.stdout.write(self.style.SUCCESS('═' * 70))
        self.stdout.write('')

        # Get domain policies
        if domain_filter:
            policies = DomainPolicy.objects.filter(domain=domain_filter)
        elif show_all:
            policies = DomainPolicy.objects.all().order_by('domain')
        else:
            # Show only domains with recent activity (last 7 days)
            cutoff = timezone.now() - timedelta(days=7)
            policies = DomainPolicy.objects.filter(
                last_request_at__gte=cutoff
            ).order_by('domain')

        if not policies.exists():
            self.stdout.write(self.style.WARNING('No domain policies found.'))
            if not show_all:
                self.stdout.write(
                    self.style.NOTICE(
                        'Tip: Use --show-all to see all domains, or --domain <name> for specific domain.'
                    )
                )
            return

        # Display each domain
        for policy in policies:
            self._display_domain(policy, last_24h)

        # Summary
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('─' * 70))
        self.stdout.write(
            f'Total domains: {policies.count()}'
        )

        # Circuit breaker summary
        open_circuits = policies.filter(circuit_state='open').count()
        if open_circuits > 0:
            self.stdout.write(
                self.style.ERROR(
                    f'⚠️  {open_circuits} domain(s) with open circuit breaker'
                )
            )

        # Cache summary
        total_cache_entries = URLCache.objects.count()
        if total_cache_entries > 0:
            recent_cache = URLCache.objects.filter(
                last_validated_at__gte=timezone.now() - timedelta(hours=24)
            ).count()
            self.stdout.write(
                f'HTTP cache: {total_cache_entries} URLs cached '
                f'({recent_cache} used in last 24h)'
            )

        self.stdout.write('')

    def _display_domain(self, policy: DomainPolicy, last_24h: bool):
        """Display detailed report for a single domain."""
        domain = policy.domain

        # Header
        self.stdout.write(self.style.HTTP_INFO(f'Domain: {domain}'))
        self.stdout.write('─' * 70)

        # Circuit breaker status
        circuit_icon = {
            'closed': '✅',
            'open': '🔴',
            'half_open': '🟡',
        }.get(policy.circuit_state, '❓')

        circuit_label = {
            'closed': 'Closed (Normal)',
            'open': 'OPEN (Blocked)',
            'half_open': 'Half-Open (Testing)',
        }.get(policy.circuit_state, policy.circuit_state)

        self.stdout.write(f'  Circuit:         {circuit_icon} {circuit_label}')

        if policy.circuit_state == 'open':
            # Show cooldown remaining
            if policy.circuit_opened_at:
                elapsed = (timezone.now() - policy.circuit_opened_at).total_seconds()
                remaining = max(0, policy.circuit_cooldown_seconds - elapsed)
                if remaining > 0:
                    self.stdout.write(
                        self.style.WARNING(
                            f'                   Cooldown: {int(remaining)}s remaining'
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.NOTICE(
                            '                   Cooldown expired, will test on next request'
                        )
                    )

        # Rate limiting status
        self.stdout.write(
            f'  Rate Limit:      {policy.max_rpm} RPM, '
            f'{policy.max_concurrent} concurrent max'
        )

        # Backoff status
        if policy.current_backoff_multiplier > 1.0:
            self.stdout.write(
                self.style.WARNING(
                    f'  Backoff:         {policy.current_backoff_multiplier:.1f}x '
                    f'(slowed down due to errors)'
                )
            )
        else:
            self.stdout.write(f'  Backoff:         {policy.current_backoff_multiplier:.1f}x (normal)')

        # Failures
        if policy.consecutive_failures > 0:
            threshold = policy.circuit_failure_threshold
            self.stdout.write(
                self.style.WARNING(
                    f'  Failures:        {policy.consecutive_failures} consecutive '
                    f'(threshold: {threshold})'
                )
            )

        # Last activity
        if policy.last_request_at:
            time_ago = self._format_time_ago(policy.last_request_at)
            self.stdout.write(f'  Last Request:    {time_ago}')
        if policy.last_success_at:
            time_ago = self._format_time_ago(policy.last_success_at)
            self.stdout.write(f'  Last Success:    {time_ago}')
        if policy.last_failure_at:
            time_ago = self._format_time_ago(policy.last_failure_at)
            self.stdout.write(
                self.style.WARNING(f'  Last Failure:    {time_ago}')
            )

        # Metrics
        if last_24h:
            cutoff = timezone.now() - timedelta(hours=24)
            metrics_qs = DomainMetrics.objects.filter(
                domain=domain,
                hour_bucket__gte=cutoff
            )
        else:
            # Last 7 days
            cutoff = timezone.now() - timedelta(days=7)
            metrics_qs = DomainMetrics.objects.filter(
                domain=domain,
                hour_bucket__gte=cutoff
            )

        if metrics_qs.exists():
            # Aggregate metrics
            total_2xx = sum(m.requests_2xx for m in metrics_qs)
            total_4xx = sum(m.requests_4xx for m in metrics_qs)
            total_5xx = sum(m.requests_5xx for m in metrics_qs)
            total_429 = sum(m.requests_429 for m in metrics_qs)
            total_403 = sum(m.requests_403 for m in metrics_qs)
            total_retries = sum(m.retry_count for m in metrics_qs)
            total_requests = total_2xx + total_4xx + total_5xx

            if total_requests > 0:
                success_rate = (total_2xx / total_requests) * 100

                self.stdout.write('')
                self.stdout.write('  Metrics (last 24h):' if last_24h else '  Metrics (last 7d):')

                # Success rate
                if success_rate >= 95:
                    style = self.style.SUCCESS
                elif success_rate >= 80:
                    style = self.style.WARNING
                else:
                    style = self.style.ERROR

                self.stdout.write(
                    f'    Total Requests: {total_requests:,} '
                    f'(success rate: {style(f"{success_rate:.1f}%")})'
                )

                # Breakdown
                if total_2xx > 0:
                    self.stdout.write(
                        f'      2xx: {total_2xx:,} ({total_2xx/total_requests*100:.1f}%)'
                    )
                if total_4xx > 0:
                    self.stdout.write(
                        self.style.WARNING(
                            f'      4xx: {total_4xx:,} ({total_4xx/total_requests*100:.1f}%)'
                        )
                    )
                    if total_429 > 0:
                        self.stdout.write(
                            self.style.ERROR(
                                f'        429 (Rate Limit): {total_429:,}'
                            )
                        )
                    if total_403 > 0:
                        self.stdout.write(
                            self.style.ERROR(
                                f'        403 (Forbidden): {total_403:,}'
                            )
                        )
                if total_5xx > 0:
                    self.stdout.write(
                        self.style.ERROR(
                            f'      5xx: {total_5xx:,} ({total_5xx/total_requests*100:.1f}%)'
                        )
                    )

                # Retries
                if total_retries > 0:
                    self.stdout.write(f'    Retries:        {total_retries:,}')

                # Average latency (simplified - just use first metric with latency)
                latency_metrics = [m for m in metrics_qs if m.latency_p50 is not None]
                if latency_metrics:
                    avg_latency = sum(m.latency_p50 for m in latency_metrics) / len(latency_metrics)
                    self.stdout.write(f'    Avg Latency:    {int(avg_latency)}ms')

        self.stdout.write('')

    def _format_time_ago(self, dt) -> str:
        """Format datetime as relative time (e.g., '2 hours ago')."""
        delta = timezone.now() - dt
        seconds = delta.total_seconds()

        if seconds < 60:
            return f'{int(seconds)}s ago'
        elif seconds < 3600:
            return f'{int(seconds / 60)}m ago'
        elif seconds < 86400:
            return f'{int(seconds / 3600)}h ago'
        else:
            days = int(seconds / 86400)
            return f'{days}d ago'
