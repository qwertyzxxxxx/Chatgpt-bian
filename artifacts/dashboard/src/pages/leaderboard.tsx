import { useGetLeaderboardStats, useListLeaderboardReviews } from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Progress } from "@/components/ui/progress";

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

export default function LeaderboardPage() {
  const stats = useGetLeaderboardStats();
  const reviews = useListLeaderboardReviews({ limit: 30 });

  const s = stats.data;
  const tradeRate = s?.reviews_24h
    ? Math.round((s.trade_24h / s.reviews_24h) * 100)
    : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Leaderboard AI 审查</h1>
        <p className="text-muted-foreground text-sm mt-1">AI 对 Leaderboard 交易者的审查决策</p>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
        {stats.isLoading ? (
          Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)
        ) : (
          <>
            <StatCard title="Pool 总数" value={s?.pool_total ?? 0} />
            <StatCard title="Pool 活跃" value={s?.pool_active ?? 0} />
            <StatCard title="审查次数 24h" value={s?.reviews_24h ?? 0} />
            <StatCard title="TRADE 决策 24h" value={s?.trade_24h ?? 0} />
            <StatCard title="NO_TRADE 决策 24h" value={s?.no_trade_24h ?? 0} />
            <StatCard
              title="Unknown 字段率 (avg)"
              value={s?.unknown_ratio_avg != null ? `${(s.unknown_ratio_avg * 100).toFixed(1)}%` : "N/A"}
            />
          </>
        )}
      </div>

      {/* Trade rate */}
      {!stats.isLoading && s && s.reviews_24h > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">TRADE 通过率 24h</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex justify-between text-sm">
              <span>TRADE</span>
              <span className="font-semibold">{tradeRate}%</span>
            </div>
            <Progress value={tradeRate} className="h-2" />
          </CardContent>
        </Card>
      )}

      {/* Recent reviews */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">最近审查记录</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {reviews.isLoading ? (
            <div className="p-4 space-y-2">
              {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-8 w-full" />)}
            </div>
          ) : !reviews.data?.length ? (
            <p className="p-4 text-sm text-muted-foreground">暂无数据</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>#</TableHead>
                  <TableHead>决策</TableHead>
                  <TableHead>候选数</TableHead>
                  <TableHead>拒绝原因</TableHead>
                  <TableHead>时间</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {reviews.data.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="text-muted-foreground text-xs">{row.id}</TableCell>
                    <TableCell>
                      <Badge variant={row.decision === "TRADE" ? "default" : "secondary"}>
                        {row.decision}
                      </Badge>
                    </TableCell>
                    <TableCell>{row.candidate_count ?? "—"}</TableCell>
                    <TableCell className="text-xs text-muted-foreground max-w-[200px] truncate">
                      {row.reject_reasons ?? "—"}
                    </TableCell>
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
