from datetime import UTC, datetime, timedelta

from fpl.api.models import Event
from fpl.cli import file_pre_deadline_copy


def _event(deadline: datetime) -> Event:
    return Event(id=10, name="Gameweek 10", deadline_time=deadline, finished=False,
                 data_checked=False, is_previous=False, is_current=False, is_next=True)


def test_pre_deadline_copy_inside_window(tmp_path):
    snap = tmp_path / "raw" / "2026-11-06" / "bootstrap_static.json.gz"
    snap.parent.mkdir(parents=True)
    snap.write_bytes(b"x")
    now = datetime(2026, 11, 6, 16, 30, tzinfo=UTC)
    dest = file_pre_deadline_copy([snap], tmp_path, _event(now + timedelta(hours=2)), now, 6)
    assert dest == tmp_path / "deadlines" / "gw10"
    assert (dest / "bootstrap_static.json.gz").read_bytes() == b"x"
    assert "2.00h before Gameweek 10" in (dest / "captured_at.txt").read_text()


def test_no_copy_outside_window_or_after_deadline(tmp_path):
    now = datetime(2026, 11, 6, 16, 30, tzinfo=UTC)
    assert file_pre_deadline_copy([], tmp_path, _event(now + timedelta(hours=10)), now, 6) is None
    assert file_pre_deadline_copy([], tmp_path, _event(now - timedelta(hours=1)), now, 6) is None
    assert not (tmp_path / "deadlines").exists()
