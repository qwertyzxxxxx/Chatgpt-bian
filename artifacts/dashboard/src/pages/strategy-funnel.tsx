import { useGetStrategyFunnel } from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";

const COLORS = [
  "hsl(var(--chart-1))",
  "hsl(var(--chart-2))",
  "hsl(var(--chart-3))",
  "hsl(var(--chart-4))",
  "hsl(var(--chart-5))",
];

export default function StrategyFunnelPage() {
  const { data, isLoading } = useGetStrategyFunnel();

  const strategies = Array.from(new Set((data?.layers ?? []).map((l) => l.strategy)));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Strategy Funnel</h1>
        <p className="text-muted-foreground text-sm mt-1">最近一次运行各策略过滤漏斗</p>
      </div>

      {isLoading ? (
        <div className="space-y-4">
          {Array.from({ length: 2 }).map((_, i) => <Skeleton key={i} className="h-48 w-full" />)}
        </div>
      ) : !data?.run_id ? (
        <Card>
          <CardContent className="p-6 text-sm text-muted-foreground">
            暂无 Funnel 数据（strategy_funnel_runs 表未找到或为空）
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="flex items-center gap-3 text-sm text-muted-foreground">
            <span>Run ID: <strong className="text-foreground">{data.run_id}</strong></span>
            <span>·</span>
            <span>开始时间: <strong className="text-foreground">{data.started_at}</strong></span>
          </div>

          {strategies.length === 0 ? (
            <Card>
              <CardContent className="p-6 text-sm text-muted-foreground">
                此次运行无漏斗层级数据
              </CardContent>
            </Card>
          ) : (
            strategies.map((strategy) => {
              const layers = (data.layers ?? []).filter((l) => l.strategy === strategy);
              const chartData = layers.map((l) => ({ name: l.layer, value: l.count }));

              return (
                <Card key={strategy}>
                  <CardHeader>
                    <CardTitle className="text-base flex items-center gap-2">
                      <Badge variant="outline">{strategy}</Badge>
                      <span className="text-muted-foreground font-normal text-sm">
                        {layers.length} 个过滤层
                      </span>
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="h-48">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
                          <XAxis
                            dataKey="name"
                            tick={{ fontSize: 11 }}
                            tickLine={false}
                            axisLine={false}
                          />
                          <YAxis
                            tick={{ fontSize: 11 }}
                            tickLine={false}
                            axisLine={false}
                            allowDecimals={false}
                          />
                          <Tooltip
                            contentStyle={{
                              background: "hsl(var(--card))",
                              border: "1px solid hsl(var(--border))",
                              borderRadius: 6,
                              fontSize: 12,
                            }}
                          />
                          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                            {chartData.map((_, idx) => (
                              <Cell key={idx} fill={COLORS[idx % COLORS.length]} />
                            ))}
                          </Bar>
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                    {/* Table */}
                    <div className="mt-4 space-y-1">
                      {layers.map((l, i) => {
                        const prev = i > 0 ? layers[i - 1].count : null;
                        const drop = prev != null && prev > 0
                          ? Math.round(((prev - l.count) / prev) * 100)
                          : null;
                        return (
                          <div key={l.layer} className="flex items-center justify-between text-sm py-1 border-b last:border-0">
                            <span className="text-muted-foreground truncate max-w-[60%]">{l.layer}</span>
                            <div className="flex items-center gap-3">
                              {drop != null && (
                                <span className="text-xs text-destructive">-{drop}%</span>
                              )}
                              <span className="font-mono font-semibold">{l.count}</span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </CardContent>
                </Card>
              );
            })
          )}

          <p className="text-xs text-muted-foreground text-right">
            数据更新时间: {data.generated_at}
          </p>
        </>
      )}
    </div>
  );
}
