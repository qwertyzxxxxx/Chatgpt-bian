import { Router, type IRouter } from "express";
import healthRouter from "./health";
import hotlistRouter from "./hotlist";
import leaderboardRouter from "./leaderboard";
import strategyRouter from "./strategy";

const router: IRouter = Router();

router.use(healthRouter);
router.use(hotlistRouter);
router.use(leaderboardRouter);
router.use(strategyRouter);

export default router;
