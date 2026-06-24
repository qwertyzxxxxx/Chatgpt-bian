import { useGetStrategyStatus } from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";

function StatusRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between py-2 border-b last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium">{value}</span>
    </div>
  );
}

export default function StrategyStatusPage() {
  const { data, isLoading } = useGetStrategyStatus();

  const committeeRate = data?.committee_24h_total
    ? Math.round((data.committee_24h_trade / data.committee_24h_total) * 100)
    : 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Strategy 状态</h1>
        <p className="text-muted-foreground text-sm mt-1">当前各策略持仓与 Committee 决策概览</p>
      </div>

      {isLoading ? (
        <div className="grid gap-4">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-32 w-full" />)}
        </div>
      ) : (
        <>
          {/* System status */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">系统概况</CardTitle>
            </CardHeader>
            <CardContent className="divide-y">
              <StatusRow
                label="最近运行时间"
                value={data?.last_run_at ?? <span className="text-muted-foreground">—</span>}
              />
              <StatusRow
                label="Hotlist 持仓中"
                value={
                  <Badge variant={data?.hotlist_open ? "default" : "secondary"}>
                    {data?.hotlist_open ?? 0} 个
                  </Badge>
                }
              />
              <StatusRow
                label="AI Macro 持仓中"
                value={
                  <Badge variant={data?.ai_macro_open ? "default" : "secondary"}>
                    {data?.ai_macro_open ?? 0} 个
                  </Badge>
                }
              />
            </CardContent>
          </Card>

          {/* Committee */}
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Gemini Committee
                {data?.committee_enabled ? (
                  <Badge className="ml-2" variant="default">已启用</Badge>
                ) : (
                  <Badge className="ml-2" variant="secondary">未检测到</Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="divide-y">
                <StatusRow label="24h 审查总数" value={data?.committee_24h_total ?? 0} />
                <StatusRow
                  label="TRADE 决策"
                  value={<span className="text-green-600 font-semibold">{data?.committee_24h_trade ?? 0}</span>}
                />
                <StatusRow
                  label="NO_TRADE 决策"
                  value={<span className="text-muted-foreground">{data?.committee_24h_no_trade ?? 0}</span>}
                />
              </div>
              {(data?.committee_24h_total ?? 0) > 0 && (
                <div className="space-y-1">
                  <div className="flex justify-between text-xs text-muted-foreground">
                    <span>TRADE 通过率</span>
                    <span>{committeeRate}%</span>
                  </div>
                  <Progress value={committeeRate} className="h-1.5" />
                </div>
              )}
            </CardContent>
          </Card>

          {/* Generated at */}
          <p className="text-xs text-muted-foreground text-right">
            数据更新时间: {data?.generated_at}
          </p>
        </>
      )}
    </div>
  );
}
