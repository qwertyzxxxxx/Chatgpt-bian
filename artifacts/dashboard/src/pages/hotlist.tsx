import {
  useGetHotlistSummary,
  useListHotlistAlerts,
  useListHotlistWatchlist,
  useGetHotlistPushPerformance,
  useGetHotlistCandidatePerformance,
  useListHotlistRecentPushedOrders,
} from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { HotlistSourcePerf } from "@workspace/api-client-react";

function StatCard({ title, value, sub }: { title: string; value: string | number; sub?: string }) {
  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {sub && <p className="text-xs text-muted-foreground mt-1">{sub}</p>}
      </CardContent>
    </Card>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  GAINER: "📈 GAINER",
  LOSER: "📉 LOSER",
  VOLUME: "🔥 VOLUME",
  UNKNOWN: "❓ UNKNOWN",
};

function SourcePerfRow({ src, perf }: { src: string; perf: HotlistSourcePerf | undefined }) {
  if (!perf || perf.total === 0) {
    return (
      <div className="flex items-center justify-between text-xs text-muted-foreground py-0.5 pl-2">
        <span>{SOURCE_LABELS[src] ?? src}</span>
        <span>—</span>
      </div>
    );
  }
  return (
    <div className="flex items-center justify-between text-xs py-0.5 pl-2">
      <span className="font-medium">{SOURCE_LABELS[src] ?? src}</span>
      <span className="text-muted-foreground">
        {perf.total}結算 {perf.win_rate}%勝
        &nbsp;TP1:{perf.tp1} TP2:{perf.tp2} SL:{perf.sl}
      </span>
    </div>
  );
}

function PerfCard({
  title,
  sub,
  tp1,
  tp2,
  sl,
  total,
  win_rate,
  open,
  by_source,
  isLoading,
}: {
  title: string;
  sub?: string;
  tp1: number;
  tp2: number;
  sl: number;
  total: number;
  win_rate: number;
  open: number;
  by_source?: { [key: string]: HotlistSourcePerf };
  isLoading: boolean;
}) {
  return (
    <Card>
      <CardHeader className="pb-1">
        <CardTitle className="text-sm font-medium text-muted-foreground">{title}</CardTitle>
        {sub && <p className="text-xs text-muted-foreground">{sub}</p>}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-6 w-24" />
            <Skeleton className="h-4 w-40" />
          </div>
        ) : (
          <>
            <div className="text-2xl font-bold">
              {win_rate}%{" "}
              <span className="text-sm font-normal text-muted-foreground">胜率</span>
            </div>
            <p className="text-xs text-muted-foreground mt-1">
              结算 {total} | TP1 {tp1} · TP2 {tp2} · SL {sl} | 持仓 {open}
            </p>
            {by_source && (
              <div className="mt-2 border-t pt-1 space-y-0.5">
                {["GAINER", "LOSER", "VOLUME"].map((src) => (
                  <SourcePerfRow key={src} src={src} perf={by_source[src]} />
                ))}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function rankTypeBadge(rankType: string | undefined) {
  if (!rankType || rankType === "UNKNOWN") return <Badge variant="outline" className="text-xs text-muted-foreground">?</Badge>;
  if (rankType === "GAINER") return <Badge variant="default" className="text-xs bg-green-600">G</Badge>;
  if (rankType === "LOSER") return <Badge variant="destructive" className="text-xs">L</Badge>;
  if (rankType === "VOLUME") return <Badge variant="secondary" className="text-xs">V</Badge>;
  return <Badge variant="outline" className="text-xs">{rankType}</Badge>;
}

function resultBadge(result: string | null | undefined) {
  if (!result) return <Badge variant="outline" className="text-muted-foreground">未结算</Badge>;
  if (result === "TP2") return <Badge className="bg-green-600 text-white">TP2</Badge>;
  if (result === "TP1") return <Badge className="bg-emerald-500 text-white">TP1</Badge>;
  if (result === "SL") return <Badge variant="destructive">SL</Badge>;
  if (result === "OPEN") return <Badge variant="secondary">OPEN</Badge>;
  return <Badge variant="outline">{result}</Badge>;
}

export default function HotlistPage() {
  const summary = useGetHotlistSummary();
  const alerts = useListHotlistAlerts({ limit: 30 });
  const watchlist = useListHotlistWatchlist();
  const pushPerf = useGetHotlistPushPerformance();
  const candidatePerf = useGetHotlistCandidatePerformance();
  const recentOrders = useListHotlistRecentPushedOrders();

  const s = summary.data;
  const pp = pushPerf.data;
  const cp = candidatePerf.data;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Hotlist 监控</h1>
        <p className="text-muted-foreground text-sm mt-1">过去 24h 扫描结果与当前活跃标的</p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {summary.isLoading ? (
          Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)
        ) : (
          <>
            <StatCard title="扫描次数 24h" value={s?.scans_24h ?? 0} />
            <StatCard title="候选标的 24h" value={s?.candidates_24h ?? 0} />
            <StatCard title="Alert 数 24h" value={s?.alerts_24h ?? 0} />
            <StatCard title="监控列表活跃" value={s?.watchlist_active ?? 0} />
            <StatCard title="持仓中 (hotlist)" value={s?.open_positions ?? 0} />
            <StatCard title="TP1 结算 24h" value={s?.settled_tp1 ?? 0} />
            <StatCard title="TP2 结算 24h" value={s?.settled_tp2 ?? 0} />
            <StatCard title="SL 止损 24h" value={s?.settled_sl ?? 0} />
          </>
        )}
      </div>

      {/* Performance cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <PerfCard
          title="📤 推送绩效"
          sub="实际 Telegram 推送订单 × strategy_results（过去 24h）"
          tp1={pp?.tp1 ?? 0}
          tp2={pp?.tp2 ?? 0}
          sl={pp?.sl ?? 0}
          total={pp?.total ?? 0}
          win_rate={pp?.win_rate ?? 0}
          open={pp?.open ?? 0}
          by_source={pp?.by_source}
          isLoading={pushPerf.isLoading}
        />
        <PerfCard
          title="📊 候选池绩效"
          sub="所有内部候选 × hotlist_outcomes（过去 24h）"
          tp1={cp?.tp1 ?? 0}
          tp2={cp?.tp2 ?? 0}
          sl={cp?.sl ?? 0}
          total={cp?.total ?? 0}
          win_rate={cp?.win_rate ?? 0}
          open={cp?.open ?? 0}
          by_source={cp?.by_source}
          isLoading={candidatePerf.isLoading}
        />
      </div>

      {/* Last 7 pushed orders */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">📌 最近7条推送订单结算</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {recentOrders.isLoading ? (
            <div className="p-4 space-y-2">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : !recentOrders.data?.length ? (
            <p className="p-4 text-sm text-muted-foreground">暂无推送订单数据</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>来源</TableHead>
                  <TableHead>Symbol</TableHead>
                  <TableHead>方向</TableHead>
                  <TableHead>买入价</TableHead>
                  <TableHead>结果</TableHead>
                  <TableHead>PnL%</TableHead>
                  <TableHead>RR实现</TableHead>
                  <TableHead>推送时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recentOrders.data.map((row, i) => (
                  <TableRow key={i}>
                    <TableCell>{rankTypeBadge(row.rank_type)}</TableCell>
                    <TableCell className="font-mono font-semibold">{row.symbol}</TableCell>
                    <TableCell>
                      <Badge variant={row.direction === "LONG" ? "default" : "destructive"}>
                        {row.direction}
                      </Badge>
                    </TableCell>
                    <TableCell className="font-mono text-xs">{row.entry ?? "—"}</TableCell>
                    <TableCell>{resultBadge(row.result)}</TableCell>
                    <TableCell className={
                      row.pnl_pct == null ? "text-muted-foreground" :
                      row.pnl_pct >= 0 ? "text-green-600 font-semibold" : "text-red-500 font-semibold"
                    }>
                      {row.pnl_pct != null ? `${row.pnl_pct >= 0 ? "+" : ""}${row.pnl_pct.toFixed(2)}%` : "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">
                      {row.rr_realized != null ? row.rr_realized.toFixed(2) : "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs">{row.pushed_at}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Top symbols */}
      {s?.top_symbols && s.top_symbols.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">24h 热门标的</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {s.top_symbols.map((t) => (
              <Badge key={t.symbol} variant="secondary">
                {t.symbol} × {t.count}
              </Badge>
            ))}
          </CardContent>
        </Card>
      )}

      {/* Watchlist */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">活跃 Watchlist</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {watchlist.isLoading ? (
            <div className="p-4 space-y-2">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : !watchlist.data?.length ? (
            <p className="p-4 text-sm text-muted-foreground">暂无数据</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead>
                  <TableHead>排名</TableHead>
                  <TableHead>观测次数</TableHead>
                  <TableHead>来源</TableHead>
                  <TableHead>首次发现</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {watchlist.data.map((row) => (
                  <TableRow key={row.symbol}>
                    <TableCell className="font-mono font-semibold">{row.symbol}</TableCell>
                    <TableCell>#{row.last_rank}</TableCell>
                    <TableCell>{row.observation_count}</TableCell>
                    <TableCell><Badge variant="outline">{row.source}</Badge></TableCell>
                    <TableCell className="text-muted-foreground text-xs">{row.first_seen_at}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Recent alerts */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">最近 Alerts</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {alerts.isLoading ? (
            <div className="p-4 space-y-2">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : !alerts.data?.length ? (
            <p className="p-4 text-sm text-muted-foreground">暂无数据</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>#</TableHead>
                  <TableHead>Symbol</TableHead>
                  <TableHead>方向</TableHead>
                  <TableHead>Level</TableHead>
                  <TableHead>时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {alerts.data.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="text-muted-foreground text-xs">{row.id}</TableCell>
                    <TableCell className="font-mono font-semibold">{row.symbol}</TableCell>
                    <TableCell>
                      <Badge variant={row.direction === "LONG" ? "default" : "destructive"}>
                        {row.direction}
                      </Badge>
                    </TableCell>
                    <TableCell>{row.level}</TableCell>
                    <TableCell className="text-muted-foreground text-xs">{row.created_at}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
