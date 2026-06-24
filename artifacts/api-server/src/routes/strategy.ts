import { Router, type IRouter } from "express";
import Database from "better-sqlite3";
import {
  GetStrategyStatusResponse,
  GetStrategyFunnelResponse,
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

router.get("/strategy/status", (_req, res): void => {
  const db = openDb(getDbPath());
  const now = new Date().toISOString().slice(0, 19);
  const window24h = new Date(Date.now() - 24 * 3600 * 1000).toISOString().slice(0, 19);

  let last_run_at: string | null = null;
  let hotlist_open = 0;
  let ai_macro_open = 0;
  let committee_enabled = false;
  let committee_24h_total = 0;
  let committee_24h_trade = 0;
  let committee_24h_no_trade = 0;

  if (db) {
    if (tableExists(db, "runner_events")) {
      const row = db
        .prepare(
          "SELECT started_at FROM runner_events ORDER BY started_at DESC LIMIT 1"
        )
        .get() as { started_at: string } | undefined;
      last_run_at = row?.started_at ?? null;
    }
    if (tableExists(db, "strategy_results")) {
      const hotlistRow = db
        .prepare(
          "SELECT COUNT(*) AS n FROM strategy_results WHERE strategy='hotlist' AND result='OPEN'"
        )
        .get() as { n: number } | undefined;
      hotlist_open = hotlistRow?.n ?? 0;

      const macroRow = db
        .prepare(
          "SELECT COUNT(*) AS n FROM strategy_results WHERE strategy='ai_macro' AND result='OPEN'"
        )
        .get() as { n: number } | undefined;
      ai_macro_open = macroRow?.n ?? 0;
    }
    if (tableExists(db, "gemini_committee_reviews")) {
      const totalRow = db
        .prepare(
          "SELECT COUNT(*) AS n FROM gemini_committee_reviews WHERE created_at >= ?"
        )
        .get(window24h) as { n: number } | undefined;
      committee_24h_total = totalRow?.n ?? 0;

      const tradeRow = db
        .prepare(
          "SELECT COUNT(*) AS n FROM gemini_committee_reviews WHERE created_at >= ? AND decision='TRADE'"
        )
        .get(window24h) as { n: number } | undefined;
      committee_24h_trade = tradeRow?.n ?? 0;
      committee_24h_no_trade = committee_24h_total - committee_24h_trade;
      committee_enabled = committee_24h_total > 0;
    }
    db.close();
  }

  const data = GetStrategyStatusResponse.parse({
    last_run_at,
    hotlist_open,
    ai_macro_open,
    committee_enabled,
    committee_24h_total,
    committee_24h_trade,
    committee_24h_no_trade,
    generated_at: now,
  });
  res.json(data);
});

router.get("/strategy/funnel", (_req, res): void => {
  const db = openDb(getDbPath());
  const now = new Date().toISOString().slice(0, 19);

  let run_id: number | null = null;
  let started_at: string | null = null;
  const layers: { strategy: string; layer: string; count: number }[] = [];

  if (db && tableExists(db, "strategy_funnel_runs") && tableExists(db, "strategy_funnel_layers")) {
    const runRow = db
      .prepare(
        "SELECT id, started_at FROM strategy_funnel_runs ORDER BY started_at DESC LIMIT 1"
      )
      .get() as { id: number; started_at: string } | undefined;

    if (runRow) {
      run_id = runRow.id;
      started_at = runRow.started_at;

      const layerRows = db
        .prepare(
          "SELECT strategy, layer, count FROM strategy_funnel_layers WHERE run_id = ? ORDER BY strategy, layer"
        )
        .all(run_id) as { strategy: string; layer: string; count: number }[];
      layers.push(...layerRows);
    }
    db.close();
  } else if (db) {
    db.close();
  }

  const data = GetStrategyFunnelResponse.parse({
    run_id,
    started_at,
    layers,
    generated_at: now,
  });
  res.json(data);
});

export default router;
