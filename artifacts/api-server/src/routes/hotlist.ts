import { Router, type IRouter } from "express";
import Database from "better-sqlite3";
import {
  GetHotlistSummaryResponse,
  ListHotlistAlertsResponse,
  ListHotlistWatchlistResponse,
} from "@workspace/api-zod";

const router: IRouter = Router();

function openDb(path: string): Database.Database | null {
  try {
    const db = new Database(path, { readonly: true, fileMustExist: true });
    db.pragma("journal_mode = WAL");
    return db;
  } catch {
    return null;
  }
}

function tableExists(db: Database.Database, name: string): boolean {
  const row = db
    .prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?")
    .get(name);
  return row !== undefined;
}

function getDbPath(): string {
  return process.env["BINANCE_DB_PATH"] ?? "/tmp/binance.db";
}

router.get("/hotlist/summary", (_req, res): void => {
  const db = openDb(getDbPath());
  const now = new Date().toISOString().slice(0, 19);
  const window24h = new Date(Date.now() - 24 * 3600 * 1000).toISOString().slice(0, 19);

  let scans_24h = 0;
  let candidates_24h = 0;
  let alerts_24h = 0;
  let open_positions = 0;
  let settled_tp1 = 0;
  let settled_tp2 = 0;
  let settled_sl = 0;
  let watchlist_active = 0;
  const top_symbols: { symbol: string; count: number }[] = [];

  if (db) {
    if (tableExists(db, "runner_events")) {
      const row = db
        .prepare(
          "SELECT COUNT(*) AS n FROM runner_events WHERE event_type='hotlist_alert' AND started_at >= ?"
        )
        .get(window24h) as { n: number } | undefined;
      scans_24h = row?.n ?? 0;
    }
    if (tableExists(db, "hotlist_opportunities")) {
      const row = db
        .prepare(
          "SELECT COUNT(*) AS n FROM hotlist_opportunities WHERE created_at >= ?"
        )
        .get(window24h) as { n: number } | undefined;
      candidates_24h = row?.n ?? 0;
    }
    if (tableExists(db, "hotlist_alerts")) {
      const row = db
        .prepare(
          "SELECT COUNT(*) AS n FROM hotlist_alerts WHERE created_at >= ?"
        )
        .get(window24h) as { n: number } | undefined;
      alerts_24h = row?.n ?? 0;

      const symbolRows = db
        .prepare(
          "SELECT symbol, COUNT(*) AS cnt FROM hotlist_alerts WHERE created_at >= ? GROUP BY symbol ORDER BY cnt DESC LIMIT 5"
        )
        .all(window24h) as { symbol: string; cnt: number }[];
      top_symbols.push(...symbolRows.map((r) => ({ symbol: r.symbol, count: r.cnt })));
    }
    if (tableExists(db, "strategy_results")) {
      const openRow = db
        .prepare(
          "SELECT COUNT(*) AS n FROM strategy_results WHERE strategy='hotlist' AND result='OPEN'"
        )
        .get() as { n: number } | undefined;
      open_positions = openRow?.n ?? 0;

      for (const [result, key] of [
        ["TP1", "tp1"] as const,
        ["TP2", "tp2"] as const,
        ["SL", "sl"] as const,
      ]) {
        const r = db
          .prepare(
            "SELECT COUNT(*) AS n FROM strategy_results WHERE strategy='hotlist' AND result=? AND closed_at >= ?"
          )
          .get(result, window24h) as { n: number } | undefined;
        if (key === "tp1") settled_tp1 = r?.n ?? 0;
        if (key === "tp2") settled_tp2 = r?.n ?? 0;
        if (key === "sl") settled_sl = r?.n ?? 0;
      }
    }
    if (tableExists(db, "hotlist_watchlist")) {
      const row = db
        .prepare(
          "SELECT COUNT(*) AS n FROM hotlist_watchlist WHERE status='ACTIVE'"
        )
        .get() as { n: number } | undefined;
      watchlist_active = row?.n ?? 0;
    }
    db.close();
  }

  const data = GetHotlistSummaryResponse.parse({
    scans_24h,
    candidates_24h,
    alerts_24h,
    open_positions,
    settled_tp1,
    settled_tp2,
    settled_sl,
    watchlist_active,
    top_symbols,
    generated_at: now,
  });
  res.json(data);
});

router.get("/hotlist/alerts", (req, res): void => {
  const limit = Math.min(Number(req.query["limit"] ?? 20), 100);
  const db = openDb(getDbPath());
  const rows: unknown[] = [];

  if (db && tableExists(db, "hotlist_alerts")) {
    const raw = db
      .prepare(
        "SELECT id, symbol, direction, entry, level, created_at FROM hotlist_alerts ORDER BY created_at DESC LIMIT ?"
      )
      .all(limit) as {
        id: number;
        symbol: string;
        direction: string;
        entry: string | null;
        level: string;
        created_at: string;
      }[];
    for (const r of raw) {
      rows.push({
        id: r.id,
        symbol: r.symbol,
        direction: r.direction,
        entry: r.entry,
        level: r.level,
        created_at: r.created_at,
        rank_score: null,
      });
    }
    db.close();
  }

  const data = ListHotlistAlertsResponse.parse(rows);
  res.json(data);
});

router.get("/hotlist/watchlist", (_req, res): void => {
  const db = openDb(getDbPath());
  const rows: unknown[] = [];

  if (db && tableExists(db, "hotlist_watchlist")) {
    const raw = db
      .prepare(
        "SELECT symbol, source, status, last_rank, observation_count, first_seen_at, expires_at FROM hotlist_watchlist WHERE status='ACTIVE' ORDER BY last_rank ASC"
      )
      .all() as {
        symbol: string;
        source: string;
        status: string;
        last_rank: number;
        observation_count: number;
        first_seen_at: string;
        expires_at: string;
      }[];
    rows.push(...raw);
    db.close();
  }

  const data = ListHotlistWatchlistResponse.parse(rows);
  res.json(data);
});

export default router;
