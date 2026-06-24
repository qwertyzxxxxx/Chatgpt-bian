import { Switch, Route, Router as WouterRouter, Link, useLocation } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import HotlistPage from "@/pages/hotlist";
import LeaderboardPage from "@/pages/leaderboard";
import StrategyStatusPage from "@/pages/strategy-status";
import StrategyFunnelPage from "@/pages/strategy-funnel";
import { cn } from "@/lib/utils";
import { BarChart2, Flame, TrendingUp, Filter, Menu, X } from "lucide-react";
import { useState } from "react";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: 30_000,
      retry: 1,
    },
  },
});

const NAV_ITEMS = [
  { path: "/", label: "Hotlist 监控", icon: Flame },
  { path: "/leaderboard", label: "Leaderboard AI", icon: TrendingUp },
  { path: "/strategy-status", label: "Strategy 状态", icon: BarChart2 },
  { path: "/strategy-funnel", label: "Strategy Funnel", icon: Filter },
];

function NavLink({ path, label, icon: Icon, onClick }: { path: string; label: string; icon: React.ElementType; onClick?: () => void }) {
  const [location] = useLocation();
  const active = location === path;
  return (
    <Link
      href={path}
      onClick={onClick}
      className={cn(
        "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
        active
          ? "bg-sidebar-accent text-sidebar-accent-foreground"
          : "text-sidebar-foreground/70 hover:bg-sidebar-accent/50 hover:text-sidebar-foreground"
      )}
    >
      <Icon className="w-4 h-4 shrink-0" />
      {label}
    </Link>
  );
}

function Sidebar({ onClose }: { onClose?: () => void }) {
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-4 py-4 border-b border-sidebar-border">
        <div>
          <div className="text-sm font-bold text-sidebar-foreground">Chatgpt-bian</div>
          <div className="text-xs text-sidebar-foreground/50 mt-0.5">Binance AI Trader</div>
        </div>
        {onClose && (
          <button onClick={onClose} className="text-sidebar-foreground/50 hover:text-sidebar-foreground">
            <X className="w-4 h-4" />
          </button>
        )}
      </div>
      <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.path} {...item} onClick={onClose} />
        ))}
      </nav>
      <div className="px-4 py-3 border-t border-sidebar-border text-xs text-sidebar-foreground/40">
        只读模式 · 自动刷新 30s
      </div>
    </div>
  );
}

function Layout({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex flex-col w-56 shrink-0 bg-sidebar border-r border-sidebar-border">
        <Sidebar />
      </aside>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 flex md:hidden">
          <div className="fixed inset-0 bg-black/40" onClick={() => setMobileOpen(false)} />
          <aside className="relative flex flex-col w-56 bg-sidebar border-r border-sidebar-border z-10">
            <Sidebar onClose={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}

      {/* Main */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Mobile topbar */}
        <header className="flex md:hidden items-center gap-3 px-4 py-3 border-b border-border bg-background shrink-0">
          <button onClick={() => setMobileOpen(true)} className="text-foreground">
            <Menu className="w-5 h-5" />
          </button>
          <span className="text-sm font-semibold">Chatgpt-bian Dashboard</span>
        </header>

        <main className="flex-1 overflow-y-auto p-4 md:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}

function Router() {
  return (
    <Layout>
      <Switch>
        <Route path="/" component={HotlistPage} />
        <Route path="/leaderboard" component={LeaderboardPage} />
        <Route path="/strategy-status" component={StrategyStatusPage} />
        <Route path="/strategy-funnel" component={StrategyFunnelPage} />
        <Route component={NotFound} />
      </Switch>
    </Layout>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
