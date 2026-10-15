import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

ETL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ETL_DIR))

import populate_ipc_arg as ipc


def observation(period, **values):
  return [period, *(values.get(column) for column in ipc.IPC_SERIES)]


class IPCETLTest(unittest.TestCase):
  def fetch(self, rows):
    response = Mock()
    response.json.return_value = {"data": rows}
    with patch.object(ipc.requests, "get", return_value=response):
      return ipc.fetch_ipc()

  def test_partial_months_survive_missing_components(self):
    with self.assertLogs(ipc.LOGGER, level="WARNING") as logs:
      frame = self.fetch([
        observation("2026-09-01", nivel_general=12500),
        observation("2026-08-01", nivel_general=12200, salud=13000),
      ])
    self.assertEqual(list(frame.fecha), [date(2026, 8, 1), date(2026, 9, 1)])
    self.assertEqual(frame.iloc[-1].nivel_general, 12500)
    self.assertTrue(pd.isna(frame.iloc[-1].salud))
    self.assertTrue(any(
      "salud" in line and ipc.IPC_SERIES["salud"] in line
      and "2026-09-01" in line for line in logs.output
    ))

  def test_complete_month_is_unchanged(self):
    row = ["2026-08-01", *range(100, 100 + len(ipc.IPC_SERIES))]
    frame = self.fetch([row])
    self.assertEqual(frame.iloc[0, 1:].tolist(), row[1:])

  def test_component_only_revision_is_kept_and_empty_month_is_dropped(self):
    frame = self.fetch([
      observation("2026-08-01", salud=13500),
      observation("2026-09-01"),
    ])
    self.assertEqual(len(frame), 1)
    self.assertEqual(frame.iloc[0].salud, 13500)

  def test_empty_and_all_null_responses_fail(self):
    for rows in ([], [observation("2026-09-01")]):
      with self.subTest(rows=rows), self.assertRaises(RuntimeError):
        self.fetch(rows)

  def test_missing_date_fails(self):
    with self.assertRaises(ValueError):
      self.fetch([observation(None, nivel_general=12500)])

  def test_upsert_preserves_existing_values_and_serializes_missing_as_null(self):
    frame = pd.DataFrame([
      observation(date(2026, 9, 1), nivel_general=12500, salud=0),
      observation(date(2026, 10, 1), nivel_general=12700),
    ], columns=["fecha", *ipc.IPC_SERIES])
    db = Mock()
    db.query.return_value = [{"affected_rows": 1}]
    # Column order should not affect the mapping to database fields.
    ipc.upsert_ipc(db, frame[frame.columns[::-1]])
    sql, values = db.query.call_args_list[0].args
    self.assertEqual(values[0], date(2026, 9, 1))
    self.assertEqual(values[1], 12500)
    self.assertIsNone(values[2])
    self.assertEqual(values[list(frame.columns).index("salud")], 0)
    for column in ipc.IPC_SERIES:
      self.assertIn(f"{column}=COALESCE(VALUES({column}), {column})", sql)
    self.assertFalse(any(pd.isna(v) for v in values if v is not None))
    self.assertEqual(db.query.call_count, 2)

  def test_empty_upsert_fails_without_writing(self):
    db = Mock()
    with self.assertRaises(RuntimeError):
      ipc.upsert_ipc(db, pd.DataFrame())
    db.query.assert_not_called()

  def test_schema_migration_preserves_types_and_date_key(self):
    db = Mock()
    db.query.return_value = [
      {"Field": "fecha", "Type": "date", "Null": "NO"},
      {"Field": "nivel_general", "Type": "decimal(10,4)", "Null": "NO"},
      {"Field": "salud", "Type": "decimal(12,5)", "Null": "NO"},
      {"Field": "bienes", "Type": "decimal(10,4)", "Null": "YES"},
    ]
    ipc.ensure_nullable_ipc_columns(db)
    sql = db.query.call_args.args[0]
    self.assertIn("MODIFY COLUMN `nivel_general` decimal(10,4) NULL", sql)
    self.assertIn("MODIFY COLUMN `salud` decimal(12,5) NULL", sql)
    self.assertNotIn("fecha", sql)
    self.assertNotIn("bienes", sql)

  def test_nullable_schema_does_not_trigger_another_migration(self):
    db = Mock()
    db.query.return_value = [
      {"Field": column, "Type": "decimal(10,4)", "Null": "YES"}
      for column in ipc.IPC_SERIES
    ]
    ipc.ensure_nullable_ipc_columns(db)
    db.query.assert_called_once_with("SHOW COLUMNS FROM ipc_argentina")

  def test_unusable_source_fails_before_commit_and_disconnects(self):
    with patch.object(ipc, "DoltDBManager") as manager, patch.object(
      ipc, "fetch_ipc", side_effect=RuntimeError("No usable IPC observations")
    ):
      with self.assertRaises(RuntimeError):
        ipc.main()
    db = manager.return_value
    db.query.assert_not_called()
    db.dolt_commit.assert_not_called()
    db.disconnect.assert_called_once()


if __name__ == "__main__":
  unittest.main()
