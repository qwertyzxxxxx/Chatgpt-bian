import { useGetHotlistSummary, useListHotlistAlerts, useListHotlistWatchlist } from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

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

export default function HotlistPage() {
  const summary = useGetHotlistSummary();
  const alerts = useListHotlistAlerts({ limit: 30 });
  const watchlist = useListHotlistWatchlist();

  const s = summary.data;

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
