"""Unit tests for time context formatting and safeguards in evelyn_server.py."""

import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch


def _make_dt(*args, tzinfo=UTC, **kwargs):
    return datetime(*args, tzinfo=tzinfo, **kwargs)


class TestTimeContext(unittest.TestCase):
    @patch("evelyn_server.datetime")
    def test_load_system_prompt_contains_date_time(self, mock_datetime):
        mock_now = datetime(2026, 7, 25, 8, 15)
        mock_datetime.now.return_value = mock_now
        mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        from evelyn_server import load_system_prompt

        sys_prompt = load_system_prompt()
        self.assertIn("The current date and time is Saturday, July 25, 2026 - 08:15 AM.", sys_prompt)

    @patch("evelyn_server.get_db")
    @patch("evelyn_server.datetime")
    def test_get_time_gap_context_formatting(self, mock_datetime, mock_get_db):
        now = datetime(2026, 7, 25, 8, 15)
        mock_datetime.now.return_value = now
        mock_datetime.fromtimestamp.side_effect = datetime.fromtimestamp
        mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        # 30 minutes ago
        ts_30m_ago = (now - timedelta(minutes=30)).timestamp()
        mock_con = MagicMock()
        mock_con.execute().fetchone.return_value = {"ts": ts_30m_ago}
        mock_get_db.return_value = mock_con

        from evelyn_server import get_time_gap_context

        ctx = get_time_gap_context()
        self.assertEqual(
            ctx,
            "[Last user message: 7:45 AM (30 minutes ago)]"
        )


if __name__ == "__main__":
    unittest.main()

