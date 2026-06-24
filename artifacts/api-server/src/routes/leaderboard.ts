import { Router, type IRouter } from "express";
import Database from "better-sqlite3";
import {
  GetLeaderboardStatsResponse,
  ListLeaderboardReviewsResponse,
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

function getLwDbPath(): string {
  return process.env["LEADERBOARD_DB_PATH"] ?? "/tmp/leaderboard.db";
}

router.get("/leaderboard/stats", (_req, res): void => {
  const db = openDb(getLwDbPath());
  const now = new Date().toISOString().slice(0, 19);
  const window24h = new Date(Date.now() - 24 * 3600 * 1000).toISOString().slice(0, 19);

  let pool_total = 0;
  let pool_active = 0;
  let reviews_24h = 0;
  let trade_24h = 0;
  let no_trade_24h = 0;
  let unknown_ratio_avg: number | null = null;

  if (db) {
    if (tableExists(db, "leaderboard_watch_items")) {
      const total = db
        .prepare("SELECT COUNT(*) AS n FROM leaderboard_watch_items")
        .get() as { n: number } | undefined;
      pool_total = total?.n ?? 0;

      const active = db
        .prepare(
          "SELECT COUNT(*) AS n FROM leaderboard_watch_items WHERE status IN ('NEW','ACTIVE')"
        )
        .get() as { n: number } | undefined;
      pool_active = active?.n ?? 0;
    }
    if (tableExists(db, "leaderboard_watch_reviews")) {
      const rev = db
        .prepare(
          "SELECT COUNT(*) AS n FROM leaderboard_watch_reviews WHERE created_at >= ?"
        )
        .get(window24h) as { n: number } | undefined;
      reviews_24h = rev?.n ?? 0;

      const trade = db
        .prepare(
          "SELECT COUNT(*) AS n FROM leaderboard_watch_reviews WHERE created_at >= ? AND decision='TRADE'"
        )
        .get(window24h) as { n: number } | undefined;
      trade_24h = trade?.n ?? 0;
      no_trade_24h = reviews_24h - trade_24h;

      if (tableExists(db, "leaderboard_watch_reviews")) {
        try {
          const fsRows = db
            .prepare(
              "SELECT field_stats FROM leaderboard_watch_reviews WHERE created_at >= ? AND field_stats IS NOT NULL"
            )
            .all(window24h) as { field_stats: string }[];
          const ratios: number[] = [];
          for (const r of fsRows) {
            try {
              const parsed = JSON.parse(r.field_stats) as Record<string, unknown>;
              const ur = parsed["unknown_ratio"];
              if (typeof ur === "number") ratios.push(ur);
            } catch {}
          }
          if (ratios.length > 0) {
            unknown_ratio_avg =
              Math.round((ratios.reduce((a, b) => a + b, 0) / ratios.length) * 10000) / 10000;
          }
        } catch {}
      }
    }
    db.close();
  }

  const data = GetLeaderboardStatsResponse.parse({
    pool_total,
    pool_active,
    reviews_24h,
    trade_24h,
    no_trade_24h,
    unknown_ratio_avg,
    generated_at: now,
  });
  res.json(data);
});

router.get("/leaderboard/reviews", (req, res): void => {
  const limit = Math.min(Number(req.query["limit"] ?? 20), 100);
  const db = openDb(getLwDbPath());
  const rows: unknown[] = [];

  if (db && tableExists(db, "leaderboard_watch_reviews")) {
    const raw = db
      .prepare(
        "SELECT id, decision, candidate_count, reject_reasons, field_stats, created_at FROM leaderboard_watch_reviews ORDER BY created_at DESC LIMIT ?"
      )
      .all(limit) as {
        id: number;
        decision: string;
        candidate_count: number | null;
        reject_reasons: string | null;
        field_stats: string | null;
        created_at: string;
      }[];
    rows.push(...raw);
    db.close();
  }

  const data = ListLeaderboardReviewsResponse.parse(rows);
  res.json(data);
});

export default router;
