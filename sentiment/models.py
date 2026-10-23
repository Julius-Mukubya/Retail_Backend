from django.db import models


class SentimentSnapshotQuerySet(models.QuerySet):
    def for_symbol(self, symbol: str):
        return self.filter(symbol=symbol.upper())

    def latest_first(self):
        return self.order_by('-fetched_at')


class SentimentSnapshot(models.Model):
    """One symbol's Myfxbook community outlook at a single poll."""
    fetched_at = models.DateTimeField(db_index=True)
    symbol = models.CharField(max_length=12)  # e.g. "EURUSD"

    long_pct = models.DecimalField(max_digits=6, decimal_places=2)
    short_pct = models.DecimalField(max_digits=6, decimal_places=2)
    long_volume = models.DecimalField(max_digits=14, decimal_places=2)   # lots
    short_volume = models.DecimalField(max_digits=14, decimal_places=2)  # lots
    long_positions = models.IntegerField()
    short_positions = models.IntegerField()
    avg_long_price = models.DecimalField(max_digits=14, decimal_places=5, null=True, blank=True)
    avg_short_price = models.DecimalField(max_digits=14, decimal_places=5, null=True, blank=True)

    raw_data = models.JSONField(null=True, blank=True)

    objects = SentimentSnapshotQuerySet.as_manager()

    class Meta:
        db_table = 'sentiment_snapshots'
        unique_together = ('fetched_at', 'symbol')
        ordering = ['-fetched_at']
        indexes = [
            models.Index(fields=['symbol', '-fetched_at']),
        ]

    def __str__(self) -> str:
        return f"{self.symbol} ({self.fetched_at:%Y-%m-%d %H:%M})"


class CurrencyExposure(models.Model):
    """Retail net exposure for one currency, derived from a poll's pair snapshots."""

    class Method(models.TextChoices):
        PERCENT = 'percent', 'Percent'
        VOLUME = 'volume', 'Volume (USD notional)'

    fetched_at = models.DateTimeField(db_index=True)
    currency = models.CharField(max_length=3)  # e.g. "EUR"
    method = models.CharField(max_length=10, choices=Method.choices)
    net_score = models.DecimalField(max_digits=18, decimal_places=4)
    # USD notional bought / sold; only filled for the volume method.
    bought = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    sold = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    pairs_used = models.JSONField(default=list)

    class Meta:
        db_table = 'currency_exposures'
        unique_together = ('fetched_at', 'currency', 'method')
        ordering = ['-fetched_at']
        indexes = [
            models.Index(fields=['currency', 'method', '-fetched_at']),
        ]

    def __str__(self) -> str:
        return f"{self.currency} {self.method} ({self.fetched_at:%Y-%m-%d %H:%M})"


class ApiCallLog(models.Model):
    """Ledger of every outbound Myfxbook request, used by the budget guard."""
    called_at = models.DateTimeField(db_index=True)
    endpoint = models.CharField(max_length=50)  # "login" or "get-community-outlook"
    status = models.CharField(max_length=20)  # "ok" or "error"
    detail = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = 'api_call_log'
        ordering = ['-called_at']

    def __str__(self) -> str:
        return f"{self.endpoint} {self.status} ({self.called_at:%Y-%m-%d %H:%M})"
