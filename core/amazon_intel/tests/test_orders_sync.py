"""
Unit tests for core/amazon_intel/spapi/orders.py

Tests:
- _parse_tsv: 10-row fixture, correct field mapping
- PII exclusion: buyer name/email/address/phone absent from output
- Idempotency: parsing same data twice produces same row set
- Currency parsing: "GBP 12.99" and "12.99" both handled
- Marketplace inference from ship_country
"""
import gzip
import os
import sys
from decimal import Decimal
from pathlib import Path

import pytest

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from core.amazon_intel.spapi.orders import (
    _parse_currency_amount,
    _parse_tsv,
    ORDERS_COLUMN_MAP,
    SHIP_COUNTRY_TO_MARKETPLACE,
)

FIXTURE_PATH = Path(__file__).parent / 'fixtures' / 'orders_sample.tsv'
FIXTURE_BYTES = FIXTURE_PATH.read_bytes()

PII_FIELDS = {
    'buyer_email', 'buyer_name', 'buyer_phone_number',
    'recipient_name',
    'ship_address_1', 'ship_address_2', 'ship_address_3',
    'ship_city', 'ship_state', 'ship_postal_code',
    'ship_phone_number',
}


class TestCurrencyParsing:
    def test_currency_with_code(self):
        amount, currency = _parse_currency_amount('GBP 12.99')
        assert amount == Decimal('12.99')
        assert currency == 'GBP'

    def test_plain_amount(self):
        amount, currency = _parse_currency_amount('12.99')
        assert amount == Decimal('12.99')
        assert currency is None

    def test_eur_with_code(self):
        amount, currency = _parse_currency_amount('EUR 14.99')
        assert amount == Decimal('14.99')
        assert currency == 'EUR'

    def test_empty_string(self):
        amount, currency = _parse_currency_amount('')
        assert amount is None
        assert currency is None

    def test_whitespace_only(self):
        amount, currency = _parse_currency_amount('   ')
        assert amount is None
        assert currency is None

    def test_usd(self):
        amount, currency = _parse_currency_amount('USD 55.00')
        assert amount == Decimal('55.00')
        assert currency == 'USD'

    def test_integer_amount(self):
        amount, currency = _parse_currency_amount('89.90')
        assert amount == Decimal('89.90')
        assert currency is None


class TestParseTsv:
    def setup_method(self):
        self.rows = _parse_tsv(FIXTURE_BYTES, 'EU')

    def test_row_count(self):
        assert len(self.rows) == 10

    def test_required_fields_present(self):
        for row in self.rows:
            assert row.get('amazon_order_id'), f"Missing amazon_order_id: {row}"
            assert row.get('order_item_id'), f"Missing order_item_id: {row}"
            assert row.get('order_date'), f"Missing order_date: {row}"
            assert row.get('region') == 'EU'

    def test_pii_fields_absent(self):
        """No PII fields should appear in parsed output."""
        for row in self.rows:
            for pii in PII_FIELDS:
                assert pii not in row, f"PII field '{pii}' found in row {row.get('amazon_order_id')}"

    def test_no_buyer_email_in_column_map(self):
        """Confirm buyer-email maps to _skip in the column map."""
        assert ORDERS_COLUMN_MAP.get('buyer-email') == '_skip'
        assert ORDERS_COLUMN_MAP.get('buyer-name') == '_skip'
        assert ORDERS_COLUMN_MAP.get('buyer-phone-number') == '_skip'
        assert ORDERS_COLUMN_MAP.get('ship-address-1') == '_skip'

    def test_currency_with_code_parsed(self):
        """Row with 'GBP 12.99' should give Decimal amount."""
        gb_row = next(r for r in self.rows if r.get('amazon_order_id') == '204-1234567-8901234')
        assert gb_row['item_price_amount'] == Decimal('12.99')
        assert gb_row['item_price_currency'] == 'GBP'

    def test_plain_amount_parsed(self):
        """Row with plain '14.99' (no currency prefix) still parses."""
        plain_row = next(r for r in self.rows if r.get('amazon_order_id') == '204-5678901-2345678')
        assert plain_row['item_price_amount'] == Decimal('14.99')

    def test_marketplace_inferred_from_ship_country(self):
        gb_row = next(r for r in self.rows if r.get('ship_country') == 'GB')
        assert gb_row['marketplace'] == 'GB'

        de_row = next(r for r in self.rows if r.get('ship_country') == 'DE')
        assert de_row['marketplace'] == 'DE'

        us_row = next(r for r in self.rows if r.get('ship_country') == 'US')
        assert us_row['marketplace'] == 'US'

    def test_is_b2b_parsed(self):
        b2b_row = next(r for r in self.rows if r.get('amazon_order_id') == '204-8901234-5678901')
        assert b2b_row['is_b2b'] is True

        normal_row = next(r for r in self.rows if r.get('amazon_order_id') == '204-1234567-8901234')
        assert normal_row['is_b2b'] is False

    def test_quantity_integer(self):
        bulk_row = next(r for r in self.rows if r.get('amazon_order_id') == '204-8901234-5678901')
        assert bulk_row['quantity'] == 10

    def test_asin_present(self):
        row = next(r for r in self.rows if r.get('amazon_order_id') == '204-1234567-8901234')
        assert row['asin'] == 'B0123456789'


class TestIdempotency:
    def test_same_data_twice_same_rows(self):
        """Parsing the same bytes twice should produce identical row sets."""
        rows1 = _parse_tsv(FIXTURE_BYTES, 'EU')
        rows2 = _parse_tsv(FIXTURE_BYTES, 'EU')
        assert len(rows1) == len(rows2)
        ids1 = {(r['amazon_order_id'], r['order_item_id']) for r in rows1}
        ids2 = {(r['amazon_order_id'], r['order_item_id']) for r in rows2}
        assert ids1 == ids2

    def test_gzipped_same_result(self):
        """Gzipped version of the fixture should produce the same rows."""
        gzipped = gzip.compress(FIXTURE_BYTES)
        rows_plain = _parse_tsv(FIXTURE_BYTES, 'EU')
        rows_gz = _parse_tsv(gzipped, 'EU')
        assert len(rows_plain) == len(rows_gz)
        ids_plain = {(r['amazon_order_id'], r['order_item_id']) for r in rows_plain}
        ids_gz = {(r['amazon_order_id'], r['order_item_id']) for r in rows_gz}
        assert ids_plain == ids_gz


class TestMarketplaceInference:
    def test_all_ship_country_mappings(self):
        expected = {
            'GB': 'GB', 'DE': 'DE', 'FR': 'FR', 'ES': 'ES', 'IT': 'IT',
            'US': 'US', 'CA': 'CA', 'AU': 'AU',
        }
        for country, expected_mkt in expected.items():
            assert SHIP_COUNTRY_TO_MARKETPLACE.get(country) == expected_mkt
