#!/usr/bin/env python3
"""
Fetch Argentina's national CPI indices from Datos Argentina and store them in Dolt.

The API republishes official INDEC data. Dates are stored exactly as returned
by the API, using the first day of each monthly observation period.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import pandas as pd
import requests

from utils.db_manager import DoltDBManager


LOGGER = logging.getLogger(__name__)

API_URL = "https://apis.datos.gob.ar/series/api/series"
DEFAULT_LAST_PERIODS = 5

# Database column -> Datos Argentina series ID.
IPC_SERIES = {
    "nivel_general": "145.3_INGNACNAL_DICI_M_15",
    "alimentos_bebidas_no_alcoholicas": "146.3_IALIMENNAL_DICI_M_45",
    "bebidas_alcoholicas_tabaco": "146.3_IBEBIDANAL_DICI_M_39",
    "prendas_vestir_calzado": "146.3_IPRENDANAL_DICI_M_35",
    "vivienda_agua_electricidad_gas_combustibles": "146.3_IVIVIENNAL_DICI_M_52",
    "equipamiento_mantenimiento_hogar": "146.3_IEQUIPANAL_DICI_M_46",
    "salud": "146.3_ISALUDNAL_DICI_M_18",
    "transporte": "146.3_ITRANSPNAL_DICI_M_23",
    "comunicacion": "146.3_ICOMUNINAL_DICI_M_27",
    "recreacion_cultura": "146.3_IRECREANAL_DICI_M_31",
    "educacion": "146.3_IEDUCACNAL_DICI_M_22",
    "restaurantes_hoteles": "146.3_IRESTAUNAL_DICI_M_33",
    "bienes_servicios_varios": "146.3_IBIENESNAL_DICI_M_36",
    "estacional": "148.3_IESTACINAL_DICI_M_25",
    "nucleo": "148.3_INUCLEONAL_DICI_M_19",
    "regulados": "148.3_IREGULANAL_DICI_M_22",
    "bienes": "147.3_IBIENESNAL_DICI_T_19",
    "servicios": "147.3_ISERVICNAL_DICI_T_22",
}


def fetch_ipc(last_periods: int = DEFAULT_LAST_PERIODS) -> pd.DataFrame:
    """
    Fetch the latest national CPI observations from Datos Argentina.

    Args:
        last_periods:
            Number of most recent monthly observations to request.
            Use a large value, such as 1000, for the initial historical load.

    Returns:
        A DataFrame whose columns match the ipc_argentina table.
    """
    if last_periods <= 0:
        raise ValueError("last_periods must be greater than zero")

    response = requests.get(
        API_URL,
        params={
            "ids": ",".join(IPC_SERIES.values()),
            "last": last_periods,
            "metadata": "none",
        },
        timeout=45,
    )
    response.raise_for_status()

    data = response.json().get("data", [])
    if not data:
        raise RuntimeError("Datos Argentina returned no IPC observations")

    columns = ["fecha", *IPC_SERIES]
    ipc = pd.DataFrame(data, columns=columns)

    ipc["fecha"] = pd.to_datetime(ipc["fecha"], errors="raise").dt.date
    if ipc["fecha"].isna().any():
        raise ValueError("Datos Argentina returned IPC observations without a date")

    for column, series_id in IPC_SERIES.items():
        missing = ipc.loc[ipc[column].isna(), "fecha"]
        if not missing.empty:
            LOGGER.warning(
                "Missing IPC values for %s (%s): %s",
                column, series_id, ", ".join(map(str, missing)),
            )

    # Components can be published at different times. Keep every available
    # observation, including component revisions without a general index.
    ipc = ipc.dropna(subset=list(IPC_SERIES), how="all")
    ipc = ipc.sort_values("fecha").reset_index(drop=True)
    if ipc.empty:
        raise RuntimeError("Datos Argentina returned no usable IPC observations")

    LOGGER.info(
        "Fetched %s IPC periods from %s through %s",
        len(ipc),
        ipc["fecha"].min(),
        ipc["fecha"].max(),
    )

    return ipc


def ensure_nullable_ipc_columns(db: DoltDBManager) -> None:
    """Allow unpublished values while preserving column types and the date key."""
    columns = db.query("SHOW COLUMNS FROM ipc_argentina")
    changes = [
        f"MODIFY COLUMN `{column['Field']}` {column['Type']} NULL"
        for column in columns
        if column["Field"] in IPC_SERIES and column["Null"] == "NO"
    ]
    if changes:
        db.query("ALTER TABLE ipc_argentina " + ", ".join(changes))
        LOGGER.info("Made %s IPC value columns nullable", len(changes))


def upsert_ipc(db: DoltDBManager, ipc: pd.DataFrame) -> None:
    """
    Insert new IPC rows and update existing rows when published values change.
    """
    if ipc.empty:
        raise RuntimeError("No usable IPC observations to store")

    value_columns = list(IPC_SERIES)
    columns = ["fecha", *value_columns]
    placeholders = ", ".join(["%s"] * len(columns))
    updates = ", ".join(
        f"{column}=COALESCE(VALUES({column}), {column})"
        for column in value_columns
    )

    sql = (
        f"INSERT INTO ipc_argentina ({', '.join(columns)}) "
        f"VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {updates}"
    )

    affected_rows = 0

    for row in ipc[columns].itertuples(index=False, name=None):
        # PyMySQL cannot serialize pandas NaN. NULL represents an unpublished
        # value on insert; COALESCE preserves existing values on update.
        values = tuple(None if pd.isna(value) else value for value in row)
        result = db.query(sql, values)
        affected_rows += result[0]["affected_rows"]

    LOGGER.info(
        "Stored %s IPC periods; Dolt reported %s affected rows",
        len(ipc),
        affected_rows,
    )


def main() -> None:
    """
    Fetch IPC data, persist it and create a Dolt commit.
    """
    last_periods = int(
        os.getenv("IPC_LAST_PERIODS", DEFAULT_LAST_PERIODS)
    )

    db = DoltDBManager()

    try:
        db.connect()

        ipc = fetch_ipc(last_periods=last_periods)
        ensure_nullable_ipc_columns(db)
        upsert_ipc(db, ipc)

        db.dolt_add("ipc_argentina")
        result = db.dolt_commit(
            "Update Argentina IPC data - "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        LOGGER.info("Dolt commit result: %s", result)

    finally:
        db.disconnect()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s:%(name)s:%(message)s",
    )
    main()
