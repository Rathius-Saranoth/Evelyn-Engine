# test_gcal_and_offer_surfacing.py
# date created: 2026-09-17
# date modified: 2026-09-17 17:31:05
# tags: #test, #gcal, #gtasks, #tool-surfacing, #temporal

"""Unit tests for GCal timezone safety, upcoming days grounding, and loop-safe assistant offer tool surfacing."""

import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from Evelyn.tools import evelyn_tools, gcal_sync, gtasks_sync, string_utils, time_manager


class TestGCalSyncAndDateParsing:
    """Tests for timezone safety, RFC 3339 formatting, and natural date parsing."""

    def test_parse_natural_date_to_dt_weekdays(self):
        """Verify natural weekday resolution relative to a fixed Thursday."""
        # 2026-09-17 is a Thursday
        ref_tz = ZoneInfo("America/Chicago")
        thursday = datetime.datetime(2026, 9, 17, 12, 0, 0, tzinfo=ref_tz)

        # Friday should resolve to tomorrow (2026-09-18)
        dt_fri = time_manager.parse_natural_date_to_dt("friday", base_dt=thursday, tz=ref_tz)
        assert dt_fri is not None
        assert dt_fri.strftime("%Y-%m-%d") == "2026-09-18"
        assert dt_fri.tzinfo == ref_tz

        # "this friday" should also resolve to tomorrow (2026-09-18)
        dt_this_fri = time_manager.parse_natural_date_to_dt("this friday", base_dt=thursday, tz=ref_tz)
        assert dt_this_fri is not None
        assert dt_this_fri.strftime("%Y-%m-%d") == "2026-09-18"

        # "next friday" should resolve to next week's Friday (2026-09-25)
        dt_next_fri = time_manager.parse_natural_date_to_dt("next friday", base_dt=thursday, tz=ref_tz)
        assert dt_next_fri is not None
        assert dt_next_fri.strftime("%Y-%m-%d") == "2026-09-25"

        # "saturday" should resolve to 2026-09-19
        dt_sat = time_manager.parse_natural_date_to_dt("saturday", base_dt=thursday, tz=ref_tz)
        assert dt_sat is not None
        assert dt_sat.strftime("%Y-%m-%d") == "2026-09-19"

        # "tomorrow" should resolve to 2026-09-18
        dt_tom = time_manager.parse_natural_date_to_dt("tomorrow", base_dt=thursday, tz=ref_tz)
        assert dt_tom is not None
        assert dt_tom.strftime("%Y-%m-%d") == "2026-09-18"

        # "today" should resolve to 2026-09-17
        dt_today = time_manager.parse_natural_date_to_dt("today", base_dt=thursday, tz=ref_tz)
        assert dt_today is not None
        assert dt_today.strftime("%Y-%m-%d") == "2026-09-17"

    def test_parse_natural_date_with_time(self):
        """Verify natural relative dates with time specifiers."""
        ref_tz = ZoneInfo("America/Chicago")
        thursday = datetime.datetime(2026, 9, 17, 10, 0, 0, tzinfo=ref_tz)

        dt = time_manager.parse_natural_date_to_dt("tomorrow at 2:30 pm", base_dt=thursday, tz=ref_tz)
        assert dt is not None
        assert dt.strftime("%Y-%m-%d %H:%M:%S") == "2026-09-18 14:30:00"

        dt2 = time_manager.parse_natural_date_to_dt("friday 14:00", base_dt=thursday, tz=ref_tz)
        assert dt2 is not None
        assert dt2.strftime("%Y-%m-%d %H:%M:%S") == "2026-09-18 14:00:00"

    def test_gcal_create_event_payload_timezone(self):
        """Verify create_gcal_event includes timeZone in request body start/end."""
        mock_service = MagicMock()
        mock_events = MagicMock()
        mock_insert = MagicMock()
        mock_insert.execute.return_value = {"id": "mock_event_123", "summary": "Coffee Grounds"}
        mock_events.insert.return_value = mock_insert
        mock_service.events.return_value = mock_events

        with (
            patch("Evelyn.tools.gcal_sync.get_gcal_service", return_value=mock_service),
            patch("sqlite3.connect"),
        ):
            res = gcal_sync.create_gcal_event(
                summary="Coffee Grounds",
                start_at="2026-09-18 14:00:00",
            )
            assert res["status"] == "success"
            assert res["event_id"] == "mock_event_123"

            call_args = mock_events.insert.call_args[1]
            body = call_args["body"]
            assert "start" in body and "end" in body
            assert body["start"].get("timeZone") == "America/Chicago"
            assert body["end"].get("timeZone") == "America/Chicago"
            assert body["start"]["dateTime"].startswith("2026-09-18T14:00:00")

    def test_gcal_sync_events_rfc3339_formatting(self):
        """Verify sync_events constructs clean RFC 3339 timestamps without duplicate +00:00Z."""
        mock_service = MagicMock()
        mock_events = MagicMock()
        mock_list = MagicMock()
        mock_list.execute.return_value = {"items": []}
        mock_events.list.return_value = mock_list
        mock_service.events.return_value = mock_events

        with (
            patch("Evelyn.tools.gcal_sync.get_gcal_service", return_value=mock_service),
            patch("sqlite3.connect"),
        ):
            gcal_sync.sync_gcal_events(days_back=7, days_forward=30)
            call_args = mock_events.list.call_args[1]
            time_min = call_args["timeMin"]
            time_max = call_args["timeMax"]

            assert time_min.endswith("Z")
            assert "+00:00" not in time_min
            assert time_max.endswith("Z")
            assert "+00:00" not in time_max

    def test_gtasks_parse_due_datetime_natural(self):
        """Verify parse_due_datetime parses natural relative dates to UTC RFC 3339."""
        with patch(
            "Evelyn.tools.time_manager.datetime"
        ) as mock_dt:
            mock_dt.now.return_value = datetime.datetime(2026, 9, 17, 12, 0, 0, tzinfo=ZoneInfo("America/Chicago"))
            mock_dt.strptime = datetime.datetime.strptime
            mock_dt.fromisoformat = datetime.datetime.fromisoformat
            mock_dt.UTC = datetime.UTC

            res = gtasks_sync.parse_due_datetime("tomorrow")
            assert res is not None
            assert res.endswith(".000Z")
            assert "2026-09-18" in res


class TestTemporalContextUpcomingDays:
    """Tests for <upcoming_days> ground-truth injection."""

    def test_get_upcoming_days_structure(self):
        """Verify upcoming days generation enumerates week with Tomorrow and next week markers."""
        ref_tz = ZoneInfo("America/Chicago")
        thursday = datetime.datetime(2026, 9, 17, 12, 0, 0, tzinfo=ref_tz)
        upcoming = time_manager.get_upcoming_days(thursday, count=8)

        assert len(upcoming) == 8
        assert upcoming[0] == "Tomorrow (Friday): 2026-09-18"
        assert upcoming[1] == "Saturday: 2026-09-19"
        assert upcoming[7] == "Friday (next week): 2026-09-25"

    def test_build_temporal_envelope_includes_upcoming_days(self):
        """Verify build_temporal_envelope wraps <upcoming_days> element cleanly."""
        xml = string_utils.build_temporal_envelope(
            current_time="Thursday, Sep 17, 2026, 5:04 PM CDT",
            upcoming_days=[
                "Tomorrow (Friday): 2026-09-18",
                "Saturday: 2026-09-19",
                "Friday (next week): 2026-09-25",
            ],
        )
        assert "<upcoming_days>" in xml
        assert "Tomorrow (Friday): 2026-09-18" in xml
        assert "Friday (next week): 2026-09-25" in xml
        assert "</upcoming_days>" in xml


class TestLoopSafeAssistantOfferSurfacing:
    """Tests for guarded assistant offer tool inheritance and anti-loop safety."""

    def test_affirmation_surfaces_offered_tool(self):
        """Verify that when user affirms an assistant proposal question, the tool is surfaced."""
        history = [
            {
                "role": "user",
                "content": "Mhmm. I just need to remember to bring home the coffee grounds from work on Fridays.",
            },
            {
                "role": "assistant",
                "content": (
                    "It is easy for things to slip through the cracks, love.\n\n"
                    "Shall I go ahead and add that to your agenda now?"
                ),
            },
        ]
        active = evelyn_tools.get_active_tools(
            user_message="Yes please. I would very much appreciate that love.",
            recent_history=history,
        )
        active_names = [evelyn_tools.extract_tool_name(t) for t in active]
        assert "create_task" in active_names

    def test_anti_loop_non_offer_assistant_prose(self):
        """Verify that assistant prose discussing a tool without an offer question does NOT leak tools."""
        history = [
            {
                "role": "user",
                "content": "How did my schedule look today?",
            },
            {
                "role": "assistant",
                "content": (
                    "I checked your calendar, and there were no meetings scheduled for this afternoon.\n"
                    "Everything remained quite peaceful."
                ),
            },
        ]
        # User says something simple without affirmation
        active = evelyn_tools.get_active_tools(
            user_message="That's good to hear.",
            recent_history=history,
        )
        active_names = [evelyn_tools.extract_tool_name(t) for t in active]
        # create_calendar_event must NOT be triggered
        assert "create_calendar_event" not in active_names
        assert "create_task" not in active_names

    def test_create_task_natural_intent_pattern(self):
        """Verify broadened create_task intent patterns directly trigger on natural user phrasing."""
        active = evelyn_tools.get_active_tools(
            user_message="I need to remember to pick up dry cleaning tomorrow",
            recent_history=[],
        )
        active_names = [evelyn_tools.extract_tool_name(t) for t in active]
        assert "create_task" in active_names

        active2 = evelyn_tools.get_active_tools(
            user_message="Please add an item to my agenda to call the mechanic",
            recent_history=[],
        )
        active_names2 = [evelyn_tools.extract_tool_name(t) for t in active2]
        assert "create_task" in active_names2
