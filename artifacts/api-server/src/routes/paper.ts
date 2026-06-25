import { Router, type IRouter } from "express";
import Database from "better-sqlite3";

const router: IRouter = Router();

function dbPath(): string {
  return process.env["BINANCE_DB_PATH"] ?? "/tmp/binance.db";
}

function openDb(path: string): Database.Database | null {
  try {
    return new Database(path, { readonly: true, fileMustExist: true });
  } catch {
    return null;
  }
}

function tableExists(db: Database.Database, table: string): boolean {
  const row = db
    .prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?")
    .get(table);
  return row != null;
}

router.get("/paper-orders", (req, res): void => {
  const db = openDb(dbPath());
  if (!db || !tableExists(db, "paper_orders")) {
    res.json({ orders: [], total: 0 });
    db?.close();
    return;
  }

  const {
    strategy_id,
    pushed,
    status,
    result,
    symbol,
    since,
    until,
    limit: limitStr = "200",
  } = req.query as Record<string, string>;

  const clauses: string[] = ["1=1"];
  const params: (string | number)[] = [];

  if (strategy_id) { clauses.push("strategy_id=?"); params.push(strategy_id); }
  if (pushed !== undefined) { clauses.push("pushed=?"); params.push(pushed === "true" ? 1 : 0); }
  if (status) { clauses.push("status=?"); params.push(status); }
  if (result) { clauses.push("result=?"); params.push(result); }
  if (symbol) { clauses.push("symbol LIKE ?"); params.push(`%${symbol}%`); }
  if (since) { clauses.push("created_at>=?"); params.push(since); }
  if (until) { clauses.push("created_at<=?"); params.push(until); }

  const limit = Math.min(parseInt(limitStr, 10) || 200, 1000);
  params.push(limit);

  try {
    const orders = db
      .prepare(
        `SELECT order_id, strategy_id, source_type, source_id,
                symbol, direction, entry, stop_loss, tp1, tp2, rr,
                status, result, pushed, alert_id,
                created_at, filled_at, closed_at, expires_at,
                pnl_pct, rr_realized, duration_minutes, legacy
         FROM paper_orders
         WHERE ${clauses.join(" AND ")}
         ORDER BY created_at DESC
         LIMIT ?`
      )
      .all(...params) as object[];

    const countRow = db
      .prepare(
        `SELECT COUNT(*) AS n FROM paper_orders WHERE ${clauses.slice(0, -1).join(" AND ") || "1=1"}`
      )
      .get(...params.slice(0, -1)) as { n: number };

    res.json({ orders, total: countRow?.n ?? orders.length });
  } catch (err) {
    res.status(500).json({ error: "query failed", detail: String(err) });
  } finally {
    db.close();
  }
});

router.get("/paper-orders/summary", (_req, res): void => {
  const db = openDb(dbPath());
  if (!db || !tableExists(db, "paper_orders")) {
    res.json({
      total: 0, open: 0, filled: 0, tp1: 0, tp2: 0, sl: 0,
      expired_not_filled: 0, timeout: 0,
      win_rate_pct: null, avg_pnl_pct: null, avg_rr: null,
      pushed_total: 0, pushed_win_rate_pct: null,
    });
    db?.close();
    return;
  }

  try {
    const rows = db
      .prepare("SELECT status, result, pushed, pnl_pct, rr_realized FROM paper_orders")
      .all() as { status: string; result: string | null; pushed: number; pnl_pct: string | null; rr_realized: string | null }[];

    db.close();

    const decisiveResults = new Set(["TP1", "TP2", "SL"]);
    const winResults = new Set(["TP1", "TP2"]);

    let total = 0, open = 0, filled_count = 0, tp1 = 0, tp2 = 0, sl = 0;
    let expired_not_filled = 0, timeout_count = 0;
    let settled: typeof rows = [];
    let pushed_total = 0;
    let pushed_settled: typeof rows = [];

    for (const r of rows) {
      total++;
      if (r.status === "OPEN") open++;
      else if (r.status === "FILLED") filled_count++;
      if (r.result === "TP1") tp1++;
      else if (r.result === "TP2") tp2++;
      else if (r.result === "SL") sl++;
      else if (r.result === "EXPIRED_NOT_FILLED") expired_not_filled++;
      else if (r.result === "TIMEOUT") timeout_count++;
      if (r.result && decisiveResults.has(r.result)) settled.push(r);
      if (r.pushed === 1) {
        pushed_total++;
        if (r.result && decisiveResults.has(r.result)) pushed_settled.push(r);
      }
    }

    const wins = settled.filter(r => winResults.has(r.result!));
    const win_rate_pct = settled.length > 0 ? Math.round(wins.length / settled.length * 100) : null;

    const pnls = settled.map(r => parseFloat(r.pnl_pct ?? "")).filter(v => !isNaN(v));
    const avg_pnl_pct = pnls.length > 0 ? Math.round(pnls.reduce((a, b) => a + b, 0) / pnls.length * 100) / 100 : null;

    const rrs = settled.map(r => parseFloat(r.rr_realized ?? "")).filter(v => !isNaN(v));
    const avg_rr = rrs.length > 0 ? Math.round(rrs.reduce((a, b) => a + b, 0) / rrs.length * 100) / 100 : null;

    const pushed_wins = pushed_settled.filter(r => winResults.has(r.result!));
    const pushed_win_rate_pct = pushed_settled.length > 0
      ? Math.round(pushed_wins.length / pushed_settled.length * 100)
      : null;

    res.json({
      total, open, filled: filled_count, tp1, tp2, sl,
      expired_not_filled, timeout: timeout_count,
      settled: settled.length,
      win_rate_pct, avg_pnl_pct, avg_rr,
      pushed_total, pushed_win_rate_pct,
    });
  } catch (err) {
    res.status(500).json({ error: "summary failed", detail: String(err) });
    db?.close();
  }
});

export default router;
