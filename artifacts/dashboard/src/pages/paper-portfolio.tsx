import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { TrendingUp, TrendingDown, Minus, Loader2, AlertCircle, ChevronUp, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");
const API = `${BASE}/api`;

interface PaperOrderSummary {
  total: number;
  open: number;
  filled: number;
  tp1: number;
  tp2: number;
  sl: number;
  expired_not_filled: number;
  timeout: number;
  settled: number;
  win_rate_pct: number | null;
  avg_pnl_pct: number | null;
  avg_rr: number | null;
  pushed_total: number;
  pushed_win_rate_pct: number | null;
}

interface PaperOrder {
  order_id: string;
  strategy_id: string;
  source_type: string;
  symbol: string;
  direction: string;
  entry: string;
  stop_loss: string;
  tp1: string;
  tp2: string;
  rr: string;
  status: string;
  result: string | null;
  pushed: number;
  created_at: string;
  filled_at: string | null;
  closed_at: string | null;
  pnl_pct: string | null;
  rr_realized: string | null;
  duration_minutes: number | null;
}

function statusBadge(status: string, result: string | null) {
  const label = result || status;
  const styles: Record<string, string> = {
    TP1: "bg-emerald-500/15 text-emerald-400",
    TP2: "bg-emerald-600/20 text-emerald-300",
    SL: "bg-red-500/15 text-red-400",
    OPEN: "bg-blue-500/15 text-blue-400",
    FILLED: "bg-yellow-500/15 text-yellow-400",
    EXPIRED_NOT_FILLED: "bg-zinc-500/15 text-zinc-400",
    TIMEOUT: "bg-orange-500/15 text-orange-400",
    CANCELLED: "bg-zinc-500/15 text-zinc-400",
  };
  return (
    <span className={cn("px-2 py-0.5 rounded text-xs font-medium", styles[label] ?? "bg-zinc-500/15 text-zinc-400")}>
      {label}
    </span>
  );
}

function dirBadge(dir: string) {
  return (
    <span className={cn("px-1.5 py-0.5 rounded text-xs font-semibold", dir === "LONG" ? "text-emerald-400" : "text-red-400")}>
      {dir}
    </span>
  );
}

function pnlCell(pnl: string | null) {
  if (!pnl) return <span className="text-zinc-500">—</span>;
  const v = parseFloat(pnl);
  const color = v > 0 ? "text-emerald-400" : v < 0 ? "text-red-400" : "text-zinc-400";
  return <span className={color}>{v > 0 ? "+" : ""}{v.toFixed(2)}%</span>;
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-card border border-border rounded-lg px-4 py-3">
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      <div className="text-xl font-bold text-foreground">{value}</div>
      {sub && <div className="text-xs text-muted-foreground mt-0.5">{sub}</div>}
    </div>
  );
}

export default function PaperPortfolioPage() {
  const [strategyFilter, setStrategyFilter] = useState("");
  const [pushedFilter, setPushedFilter] = useState<"all" | "pushed" | "candidate">("all");
  const [statusFilter, setStatusFilter] = useState("");
  const [symbolFilter, setSymbolFilter] = useState("");
  const [sortDir, setSortDir] = useState<"desc" | "asc">("desc");

  const summaryQ = useQuery<PaperOrderSummary>({
    queryKey: ["paper-summary"],
    queryFn: () => fetch(`${API}/paper-orders/summary`).then(r => r.json()),
    refetchInterval: 30_000,
  });

  const params = new URLSearchParams({ limit: "200" });
  if (strategyFilter) params.set("strategy_id", strategyFilter);
  if (pushedFilter === "pushed") params.set("pushed", "true");
  if (pushedFilter === "candidate") params.set("pushed", "false");
  if (statusFilter) params.set("status", statusFilter);
  if (symbolFilter) params.set("symbol", symbolFilter);

  const ordersQ = useQuery<{ orders: PaperOrder[]; total: number }>({
    queryKey: ["paper-orders", strategyFilter, pushedFilter, statusFilter, symbolFilter],
    queryFn: () => fetch(`${API}/paper-orders?${params}`).then(r => r.json()),
    refetchInterval: 30_000,
  });

  const s = summaryQ.data;
  const orders = ordersQ.data?.orders ?? [];
  const sorted = [...orders].sort((a, b) => {
    const cmp = a.created_at.localeCompare(b.created_at);
    return sortDir === "desc" ? -cmp : cmp;
  });

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold text-foreground">模拟仓 Paper Portfolio</h1>
        <p className="text-sm text-muted-foreground mt-1">
          基于真实 K 线验证的模拟订单 · 成交确认后才计算 TP/SL · 非成交订单不计入胜率
        </p>
      </div>

      {/* Summary Cards */}
      {summaryQ.isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm"><Loader2 className="w-4 h-4 animate-spin" />加载统计中…</div>
      ) : summaryQ.isError ? (
        <div className="flex items-center gap-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" />统计加载失败</div>
      ) : s ? (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <StatCard label="总订单" value={s.total} />
          <StatCard label="持仓中" value={s.open + s.filled} sub={`OPEN ${s.open} · FILLED ${s.filled}`} />
          <StatCard label="TP1+TP2" value={s.tp1 + s.tp2} sub={`TP1 ${s.tp1} · TP2 ${s.tp2}`} />
          <StatCard label="止损 SL" value={s.sl} />
          <StatCard label="未成交" value={s.expired_not_filled} sub="不计入胜率" />
          <StatCard label="整体胜率" value={s.win_rate_pct !== null ? `${s.win_rate_pct}%` : "—"} sub={`结算 ${s.settled} 笔`} />
          <StatCard label="平均收益" value={s.avg_pnl_pct !== null ? `${s.avg_pnl_pct > 0 ? "+" : ""}${s.avg_pnl_pct}%` : "—"} />
          <StatCard label="平均 RR" value={s.avg_rr !== null ? `${s.avg_rr > 0 ? "+" : ""}${s.avg_rr}` : "—"} />
          <StatCard label="推送订单" value={s.pushed_total} sub={`Telegram 推送`} />
          <StatCard label="推送胜率" value={s.pushed_win_rate_pct !== null ? `${s.pushed_win_rate_pct}%` : "—"} sub="pushed only" />
        </div>
      ) : null}

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <select
          value={strategyFilter}
          onChange={e => setStrategyFilter(e.target.value)}
          className="bg-card border border-border rounded-md px-3 py-1.5 text-sm text-foreground focus:outline-none"
        >
          <option value="">全部策略</option>
          <option value="hotlist">Hotlist</option>
          <option value="baseline_v1">Baseline V1</option>
        </select>

        <div className="flex rounded-md border border-border overflow-hidden text-sm">
          {(["all", "pushed", "candidate"] as const).map(opt => (
            <button
              key={opt}
              onClick={() => setPushedFilter(opt)}
              className={cn("px-3 py-1.5 text-sm transition-colors",
                pushedFilter === opt
                  ? "bg-primary text-primary-foreground"
                  : "bg-card text-muted-foreground hover:text-foreground")}
            >
              {opt === "all" ? "全部" : opt === "pushed" ? "📢 推送" : "🔍 候选池"}
            </button>
          ))}
        </div>

        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          className="bg-card border border-border rounded-md px-3 py-1.5 text-sm text-foreground focus:outline-none"
        >
          <option value="">全部状态</option>
          <option value="OPEN">OPEN</option>
          <option value="FILLED">FILLED</option>
          <option value="TP1">TP1</option>
          <option value="TP2">TP2</option>
          <option value="SL">SL</option>
          <option value="EXPIRED_NOT_FILLED">EXPIRED_NOT_FILLED</option>
          <option value="TIMEOUT">TIMEOUT</option>
        </select>

        <input
          value={symbolFilter}
          onChange={e => setSymbolFilter(e.target.value)}
          placeholder="搜索交易对…"
          className="bg-card border border-border rounded-md px-3 py-1.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none w-36"
        />

        <button
          onClick={() => setSortDir(d => d === "desc" ? "asc" : "desc")}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground border border-border rounded-md px-2 py-1.5 bg-card"
        >
          时间 {sortDir === "desc" ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />}
        </button>

        {ordersQ.data && (
          <span className="text-xs text-muted-foreground ml-auto">
            显示 {sorted.length} / 共 {ordersQ.data.total}
          </span>
        )}
      </div>

      {/* Orders Table */}
      {ordersQ.isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground text-sm"><Loader2 className="w-4 h-4 animate-spin" />加载订单中…</div>
      ) : ordersQ.isError ? (
        <div className="flex items-center gap-2 text-red-400 text-sm"><AlertCircle className="w-4 h-4" />订单加载失败</div>
      ) : (
        <div className="bg-card border border-border rounded-lg overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-xs text-muted-foreground">
                <th className="px-4 py-2 text-left">交易对</th>
                <th className="px-4 py-2 text-left">方向</th>
                <th className="px-4 py-2 text-left">策略</th>
                <th className="px-4 py-2 text-right">入场</th>
                <th className="px-4 py-2 text-right">TP1</th>
                <th className="px-4 py-2 text-right">SL</th>
                <th className="px-4 py-2 text-right">RR</th>
                <th className="px-4 py-2 text-center">状态</th>
                <th className="px-4 py-2 text-right">收益</th>
                <th className="px-4 py-2 text-right">时长</th>
                <th className="px-4 py-2 text-center">推送</th>
                <th className="px-4 py-2 text-left">创建时间</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {sorted.length === 0 ? (
                <tr>
                  <td colSpan={12} className="px-4 py-8 text-center text-muted-foreground">
                    暂无数据
                  </td>
                </tr>
              ) : (
                sorted.map(o => (
                  <tr key={o.order_id} className="hover:bg-muted/30 transition-colors">
                    <td className="px-4 py-2 font-mono text-xs font-medium text-foreground">{o.symbol}</td>
                    <td className="px-4 py-2">{dirBadge(o.direction)}</td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">{o.strategy_id}</td>
                    <td className="px-4 py-2 text-right font-mono text-xs">{parseFloat(o.entry).toFixed(4)}</td>
                    <td className="px-4 py-2 text-right font-mono text-xs text-emerald-400/80">{parseFloat(o.tp1).toFixed(4)}</td>
                    <td className="px-4 py-2 text-right font-mono text-xs text-red-400/80">{parseFloat(o.stop_loss).toFixed(4)}</td>
                    <td className="px-4 py-2 text-right text-xs">{parseFloat(o.rr).toFixed(2)}</td>
                    <td className="px-4 py-2 text-center">{statusBadge(o.status, o.result)}</td>
                    <td className="px-4 py-2 text-right">{pnlCell(o.pnl_pct)}</td>
                    <td className="px-4 py-2 text-right text-xs text-muted-foreground">
                      {o.duration_minutes != null ? `${o.duration_minutes}m` : "—"}
                    </td>
                    <td className="px-4 py-2 text-center">
                      {o.pushed ? (
                        <span className="text-blue-400 text-xs font-medium">📢</span>
                      ) : (
                        <span className="text-zinc-600 text-xs">—</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground whitespace-nowrap">
                      {o.created_at.replace("T", " ").slice(0, 16)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
