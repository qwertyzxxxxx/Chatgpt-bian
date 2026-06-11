# Export Manifest

This manifest describes the clean source export prepared for the fresh GitHub repository
`qwertyzxxxxx/Btc-a1`.

## Export scope

- Generated at: `2026-06-11T03:18:59Z`
- Source branch: `work`
- Archive root directory: `Btc-a1/`
- Exported files: **134** (including this manifest)
- Checksum algorithm: SHA-256
- Git metadata is intentionally excluded; initialize a fresh repository after extraction.
- Runtime/private artifacts are excluded: `data/*.db`, `data/*.db-*`, `.env`, `.venv/`,
  logs, Python bytecode/cache directories, test caches, build output, and package metadata.
- `.env.example` and `data/.gitkeep` are included because they are safe project templates.

## Import into the fresh repository

```bash
tar -xzf Btc-a1-current-branch.tar.gz
cd Btc-a1
git init -b main
git add .
git commit -m "Import Binance AI Trader source export"
git remote add origin https://github.com/qwertyzxxxxx/Btc-a1.git
git push -u origin main
```

Before committing, verify the archive with the procedure below.

## Checksum verification

Every ordinary file uses the SHA-256 of its exact bytes. Because a file cannot contain its own
literal checksum without recursion, `EXPORT_MANIFEST.md` uses a canonical self-checksum: replace
the checksum in its own table row with 64 zeroes, then hash the resulting UTF-8 bytes.

Run this from the extracted `Btc-a1/` directory:

```bash
python - <<'PY'
import hashlib
import re
from pathlib import Path

manifest = Path("EXPORT_MANIFEST.md")
text = manifest.read_text(encoding="utf-8")
pattern = r"(`EXPORT_MANIFEST\.md` \| \d+ \| `)[0-9a-f]{64}(`)"
match = re.search(pattern, text)
assert match, "manifest self-checksum row not found"
expected_self = match.group(0).rsplit("`", 2)[1]
canonical = re.sub(pattern, lambda m: m.group(1) + "0" * 64 + m.group(2), text, count=1)
assert hashlib.sha256(canonical.encode()).hexdigest() == expected_self

in_table = False
checked = 0
for line in text.splitlines():
    if line == "| File | Bytes | SHA-256 |":
        in_table = True
        continue
    if not in_table or line.startswith("|---") or not line.startswith("| `"):
        continue
    path_text, size_text, digest_text = [part.strip() for part in line.strip("|").split("|")]
    relative = path_text.strip("`")
    if relative == "EXPORT_MANIFEST.md":
        checked += 1
        continue
    payload = Path(relative).read_bytes()
    assert len(payload) == int(size_text), relative
    assert hashlib.sha256(payload).hexdigest() == digest_text.strip("`"), relative
    checked += 1
print(f"verified {checked} exported files")
PY
```

## File inventory

| File | Bytes | SHA-256 |
|---|---:|---|
| `.env.example` | 431 | `704fed2dc27ee6acea1058bd25920772e186417f7d21239896bd5868e2f779b2` |
| `.gitignore` | 107 | `f65a695cb20d10957ed80c19ff24e451cb9d713155a29dedaa5618aa6b679223` |
| `.gitkeep` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `README.md` | 24870 | `975a99e940a1633961f55fb5e51f6eb27d43c2102f348c26f0b6920e96fd7ebe` |
| `architecture.md` | 24941 | `7f80ea06199e0c58aef52dbd6db696e34363237915953eba1f1cc9ed313ac974` |
| `config/sectors.json` | 777 | `1e33f8d22ddceccec67c1bfa2d573fe7ed149ed84a0765071c1a188cf4b63a5d` |
| `config/strategies/baseline_v1.json` | 544 | `a084040f829f5627cdf68d94d03dcaf56d9dc195bcdcef9abf4decc3cbd5b943` |
| `config/universe.json` | 355 | `fba92c8c33e2ce8fc544f005117cab9aaf245f32e1a7f5b29b8b9667bf017acb` |
| `data/.gitkeep` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `docs/project_audit.md` | 36821 | `f45988289027f294b5734a23decb3663d4051d816e8aa5855f4828aac4c694d8` |
| `docs/replit_deploy.md` | 9569 | `66f23757ec7c974f3d98dc2ed6cc799266fef87e962dcbcf7e0189f5abefebf7` |
| `docs/replit_reserved_vm.md` | 5758 | `3b1a9037f48ac5762be5730f1f32a91f097cf6d6a6d67045339d128d84d8d889` |
| `docs/run_collect_history_replit.md` | 16141 | `8f374a8093a19cc3b4156078f25e0dac4ef441b40cc27c7e198152ea53c1e065` |
| `docs/runbook.md` | 8295 | `95d88867f555f773c1b7d6b34b5f3f912e10c013260177b936aa12fcc0fc993e` |
| `pyproject.toml` | 566 | `c1cf2b9b061f17cea3675e54f592b22ce5e38c3a52dafe66272916d71e045f6f` |
| `reports/collected_database_readiness.md` | 6463 | `691847459c5f8aa4f83bbd6759796b810cf122c80ab211c68c00b72bf1cf3f14` |
| `reports/historical_data_collector_report.md` | 6641 | `e2639db823bfd28821fd75a926a1f897d039e534f761c3fa767bf768c371d3c2` |
| `reports/p0_capital_flow_coverage_report.md` | 11804 | `9ba5fa7e7f5e0d2377a99fcdcfe245f5fcdfcc384306046649bab42a7f852830` |
| `reports/p0_data_quality_status_report.md` | 9109 | `b4c17b8b8a14687f26c64fdcb8833297b437ad6fcaaedfdf2aef5db3a39df124` |
| `reports/p0_snapshot_lineage_report.md` | 8460 | `b4a0fc570d168cb87106b2afcf9541e26b521cd755ebe30870c06368a0883864` |
| `reports/walk_forward_data_gap.md` | 6981 | `ba26d1fef98139010919614c5d0407df60e0bc0d2b96d85831f4b70ed985021c` |
| `reports/walk_forward_validation.md` | 3502 | `25738585e782054270f03dd0c459cf2a87d68787d2c47fc2c47dc71612e8a3af` |
| `roadmap.md` | 18484 | `8081b85ca6ba1d4bf83df88bfac35db0c2b7ef33ae7b7123c48e66ab227b2800` |
| `scripts/smoke_test.sh` | 883 | `61cd43fa5f58348fc21e49d4f12bde1201ad812007fe63a7dfc033495bf7fdd5` |
| `src/binance_ai_trader/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `src/binance_ai_trader/__main__.py` | 77 | `655c859fd1fbb4adbdd4e11a9584b58da0dcba6e7b5fc00c2a52b2df7ee3fc7e` |
| `src/binance_ai_trader/application/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `src/binance_ai_trader/application/analyze_capital_flow.py` | 4135 | `463e4b87831181424dfe3b819c03d3f49f5217dcdf1de6ce98941d1afd662475` |
| `src/binance_ai_trader/application/analyze_market_regime.py` | 2476 | `17ce273b1c05bb34882d3159a2ed0aaa5a005abe8621fb6273ead0a057973ae0` |
| `src/binance_ai_trader/application/analyze_sector_strength.py` | 1248 | `9428fea4ea64bf2d38aeddf4b475dcdc20d4a64fef0630a1935e1aae4b5db8ad` |
| `src/binance_ai_trader/application/analyze_space.py` | 1955 | `9a0e74bf4605a0bc90c107a9447b4af3034c72dba34422f9a68c69206f754da7` |
| `src/binance_ai_trader/application/collect_history.py` | 13685 | `8e1f962f1370bdede8cf98a0da9caa0e1f12cec6c2eb53e0c53e7cea4c2ca1dd` |
| `src/binance_ai_trader/application/collect_market_data.py` | 3477 | `e3d5ae445ab3a83f47134e12662989a5791064d56b7b76ef939c0fe2ddba7325` |
| `src/binance_ai_trader/application/evaluate_signals.py` | 1315 | `299d79624d2d4a85cc913c73456625efaf77832be35261fb87723741d5aa9779` |
| `src/binance_ai_trader/application/generate_signals.py` | 8644 | `9b49e2febb29d2ed36f5fa8ab36383adaf3244ff06e54f987419ac6c749ec8b3` |
| `src/binance_ai_trader/application/score_market_data.py` | 2392 | `22ae31138c408bf617d995b810d286ad917c9ece6eacb6f9e54eabf0cc92c464` |
| `src/binance_ai_trader/backtest/__init__.py` | 165 | `2401d3c8eeca9c08682bf19f4e97b4cb817f3322b20395002f5991487a7365dd` |
| `src/binance_ai_trader/backtest/engine.py` | 19408 | `c6a21b5aa49bd932745dfb89e931b74acca462027f98078dbd914a837484f3a0` |
| `src/binance_ai_trader/capital/__init__.py` | 352 | `eb9536c5396282fdf003c74eca68486f028a03dac0b832b6579d67a6ed2fcb0b` |
| `src/binance_ai_trader/capital/engine.py` | 3373 | `45395ab63bcfbab84a4390510da5b21e1cda96578ff0e0bc122e0db97ef76cdf` |
| `src/binance_ai_trader/capital/history.py` | 1550 | `70225c8c8c9e8eccf44cedf3f14eb2be8f608f986c66e08c3b96b28d7cbb015a` |
| `src/binance_ai_trader/config.py` | 1290 | `4746e8d1741c3bfe5eef3db2160fefe687771cb93d9c81aded620030763269ec` |
| `src/binance_ai_trader/data_quality.py` | 744 | `eb03b419899227904f5be3d183952bcedfc7dbe18321c21b593a5e048d916f39` |
| `src/binance_ai_trader/domain/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `src/binance_ai_trader/domain/models.py` | 5856 | `5a4db08083adfd49d54d67bd5ee318b0893ae25b08e097424c07f99875d5a4cc` |
| `src/binance_ai_trader/domain/universe.py` | 1418 | `b93fe06ca87299a38a2c6999ed215600f8a834963719401a749792fea4c91acc` |
| `src/binance_ai_trader/entrypoints/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `src/binance_ai_trader/entrypoints/cli.py` | 30751 | `52ebf9838af332c5cf2067b7448fbd23a50c21162905bedfe0731125907d25de` |
| `src/binance_ai_trader/evaluation/__init__.py` | 147 | `5ed7f3727125e6aae7249143b1fd4b16ab3e84593f1f128f8375e086bd95d2b1` |
| `src/binance_ai_trader/evaluation/engine.py` | 4951 | `617a020fda003a190e26ab7154fa4873dcce4406393713933e8fc6ebfacb0531` |
| `src/binance_ai_trader/infrastructure/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `src/binance_ai_trader/infrastructure/binance_public.py` | 10874 | `4432fb2fb19af050246cd6629d45ee6c07aa31c8edd13d25a862be73d9e667ee` |
| `src/binance_ai_trader/infrastructure/sqlite_repository.py` | 99498 | `de875c7989abc4e0e6b1a6b681932d6a187e294878d4b4dceaaa5cb75b762125` |
| `src/binance_ai_trader/paper/__init__.py` | 164 | `67493898bf5cc49bb09422fa0e1e86d66a2ab5bc011f7b4c590b7a735db1fc71` |
| `src/binance_ai_trader/paper/models.py` | 953 | `4ef454915266bb53b4539c8151a9060e99ca05cb5f699b08fdf4685c2dc8f0f5` |
| `src/binance_ai_trader/paper/service.py` | 4831 | `18259929564e9e2a69567cb5cec5b867e9d09ed8b1c957dbebcd35aac9974a91` |
| `src/binance_ai_trader/regime/__init__.py` | 155 | `b8c7620ff70501bfdd47a587a922813ca81bad476d06901b4708d89ae72e0b2a` |
| `src/binance_ai_trader/regime/engine.py` | 4660 | `4eec20dd6a20267b6c0342b9dbbdcaf6b9e888cb8cefc78d5dd6ac28584abf84` |
| `src/binance_ai_trader/reporting/__init__.py` | 99 | `9c94f415f8778fb4e81f690335cfe39cfea9e6a6bc69feba34dd33e9af8d7507` |
| `src/binance_ai_trader/reporting/daily.py` | 1831 | `ef1367cf6763cdc76f481d6c6e85a5a63c429f8c2f04d0d512586141ef17bd05` |
| `src/binance_ai_trader/runner/__init__.py` | 356 | `4b2eae9e57d37f4ffe0b45e3bffa6e148a79ba8a27c0bb4e886c864471c4ba1c` |
| `src/binance_ai_trader/runner/engine.py` | 5670 | `c58c40a7558287d4d676f47c66ea07e095707211d8c886719514dc7b71a59a96` |
| `src/binance_ai_trader/runner/health.py` | 1346 | `5bc08a50d8ca925cad989d5922d95cf48baee26e1cb85cd99cc1f4b70e47e38e` |
| `src/binance_ai_trader/scoring/__init__.py` | 563 | `8269c6633cf36ce932894f45588104a9fb1d9e4bebe623004ca557875d389dce` |
| `src/binance_ai_trader/scoring/common.py` | 2468 | `45e8ab485845d50521294146165dd49f073f633191653e150515a7d9732d2dff` |
| `src/binance_ai_trader/scoring/engine.py` | 3203 | `92d6fd0c27f4138019df99de0bcd47b915913890e9ec1bd970bec52179b54fee` |
| `src/binance_ai_trader/scoring/momentum_score.py` | 1302 | `dc05b3535e23c35564d39bcd30de6e7b236a3ca351e6aecb5243f21711471b9b` |
| `src/binance_ai_trader/scoring/risk_score.py` | 2117 | `8257b698aad10156801e8163ad3af14f511eab9d9ff994fb4596456a0cafb4b3` |
| `src/binance_ai_trader/scoring/structure_score.py` | 1520 | `21e8822e2c30e4f4c1aa6f42ea774c4997a7a06bf58cc26f3c5e246d3c25e5de` |
| `src/binance_ai_trader/scoring/trend_score.py` | 1458 | `ea136c1e28bb690890f88ab98df131a910291225ee059a6614db5ddb581dee04` |
| `src/binance_ai_trader/scoring/volume_score.py` | 1805 | `f84c7343a5a8f3022a3eb9474bfdeb6482a45c7bce4cfb4dca166d356ccff4bc` |
| `src/binance_ai_trader/sectors/__init__.py` | 146 | `8eb48ebee483359c1239544d6483d297fba3b81c5fd29ae6bac84bae92d64ef5` |
| `src/binance_ai_trader/sectors/engine.py` | 3836 | `1c0efef93258ceb6a20925257ba3f8527ddab0b73b6e29eaa586b796facba747` |
| `src/binance_ai_trader/signals/__init__.py` | 416 | `35b0479c23ffa699c4b74682c88875fe6e846b25e6465bab93c8e6187f4b8f95` |
| `src/binance_ai_trader/signals/engine.py` | 9831 | `a7e16461f9303e25620662f169160610ec2d4ca4031ee3bb29da63d5a9f311fb` |
| `src/binance_ai_trader/signals/ranking.py` | 1003 | `31f4d974036fa93f5df86bf409acded2a3e4cc8dde242410807fbefee777286b` |
| `src/binance_ai_trader/signals/regime_gate.py` | 1054 | `3cdd60d96749aede8d17f54bf4d974a4bf8ba183d8399ac61b6abe97425322ad` |
| `src/binance_ai_trader/signals/sector_gate.py` | 657 | `867c73e25da83a40129a4703ffb14245c187507cb5a81891807f07555c198f7f` |
| `src/binance_ai_trader/signals/short_engine.py` | 5971 | `aef2d9c03d0c1f6793314ea59af21734c5dc49b9b759eac7095f8e382d3d93ae` |
| `src/binance_ai_trader/space/__init__.py` | 114 | `9611d9f52f385eefbf0d3da5c8a114734f0c0beacd5b076db26e18571c3d5f6c` |
| `src/binance_ai_trader/space/engine.py` | 2390 | `6cbd280b5a92821722a2e80cdba72a892727fb7368adf091790698d0321c5cb9` |
| `src/binance_ai_trader/strategy_lab/__init__.py` | 427 | `33fb73a1ffd114e603a93d8763f43fbd2175a07f2df3778d6cf4759b636ce7aa` |
| `src/binance_ai_trader/strategy_lab/config.py` | 3579 | `e18ce8c063b4578463e2d3109656631edc912622991e59ead96873015cd3e8f5` |
| `src/binance_ai_trader/strategy_lab/models.py` | 526 | `acf691cc3e445d66bd1c8753f5ea759cd538a1c71c1021257b96a91f33811d72` |
| `src/binance_ai_trader/strategy_lab/service.py` | 8144 | `cbf08739aba53ff353e87fafca2028bc45d8200c1b116e3a01bc35666524b97a` |
| `src/binance_ai_trader/walk_forward/__init__.py` | 461 | `99bcdacae74aa25c9b66f369e0f9bdae8fcee7e699dbffecddcc9f2af847eed6` |
| `src/binance_ai_trader/walk_forward/engine.py` | 11138 | `f6e2216f1aa3c1da36b7bf664e85ff5b66a1a26711585e082bf58d611287b0f5` |
| `tests/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `tests/fixtures/exchange_info.json` | 407 | `9ce67a1a0d9844bd8672d0aa17664a35d59145b69df488c416b029838f7fba8f` |
| `tests/fixtures/klines.json` | 287 | `497ce2bb1777c8f0e48d2da1ef171fe81f22647147c0121e1fe8561ca9dfe8f7` |
| `tests/fixtures/tickers_24h.json` | 136 | `51ff6254aa1f339a8d9135475247672235b56df3d8a25060c80018d938cabda4` |
| `tests/integration/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `tests/integration/test_backtest.py` | 6392 | `9f0c62cd69653022d539b0f20fe07b5a1158aef30999259912f4e19fd34f8b7a` |
| `tests/integration/test_capital_history.py` | 9933 | `30274135cb2c706b96c69077c698a125f2c9a9882e50f0fb9361f8019d8f9432` |
| `tests/integration/test_capital_space.py` | 2278 | `444815837d728c44595e986c3cc486ddf3c75f74dbf3a24ca4af6c36119d3d88` |
| `tests/integration/test_collection.py` | 3573 | `f9d432083ace0f167838e29e2b6cd9be76fe8a4d6c7676093b8c76d4bd508a93` |
| `tests/integration/test_data_quality_status.py` | 8024 | `81be419401fbc04ad89a8a30be44d2ac46c66c4b6b33df0f539ef49e713bdbdb` |
| `tests/integration/test_evaluation.py` | 6055 | `3141f3adc00ade0f491208ab6c0dd996690174e88d97530d15bb163581751dab` |
| `tests/integration/test_health.py` | 1858 | `83688b4a5c067e70cb26c25b6458468dec6993cabe8fa0db5cc7c3b56595bc92` |
| `tests/integration/test_historical_collection.py` | 6039 | `7bc52ada4456786b1e636d3dd4c33ce90ca5d2ba68a6106a69cc0f37819eeff7` |
| `tests/integration/test_regime.py` | 2936 | `da9d6888ec1cb31416d7b170f6d2c2e8fbae2d3470be4a499f0b789092f12f21` |
| `tests/integration/test_regime_signal_gate.py` | 3970 | `ec29125af44e4f1dba6a76f63bb25b4ed16bf823e865799931855d1e0245b67d` |
| `tests/integration/test_research_paper_report.py` | 4375 | `e279b9a9e53455f002db6aa2ff15b4506d94163c2811a6a1c8bbe4aa8c412b3e` |
| `tests/integration/test_scoring.py` | 3486 | `9f8e4cf118afd11ed107b4d94dd4a61372af75c32de9875b0bce49f3b9845e76` |
| `tests/integration/test_sector_signal_gate.py` | 6732 | `39ff8162c373eeb9b1498b333c59a25acd3c2da54fe472f86f4226d2c4f2d465` |
| `tests/integration/test_sectors.py` | 5555 | `92ab8065f77c2920cc734dc5ac17ccbecf4efa9614d89f2fd5c9d4457bace615` |
| `tests/integration/test_short_signal_persistence.py` | 2950 | `9be4569206ebf71df543a68417bc06f75be450bf95c070a56a166d7a00426a76` |
| `tests/integration/test_signals.py` | 4515 | `4cbe058c6866928097677482d56d7efd286e76021c45b80353162af3c0369005` |
| `tests/integration/test_snapshot_lineage.py` | 5092 | `68d4508346f5e27d3799380b1afafa528aa4e754fe08f35096b55f8ac3550890` |
| `tests/integration/test_strategy_lab.py` | 3045 | `ca941b386d09d4fd2c2c5bfaf1e9857a979f799000f3f4efab106cc589ff1b9b` |
| `tests/integration/test_walk_forward.py` | 3350 | `1666643c3fb414f330609e71a9e8bd124d2deb2048e3fe4222d827ae4295f7d6` |
| `tests/unit/__init__.py` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `tests/unit/test_backtest_engine.py` | 2962 | `430d6da5e1ddd60bed0f30904f7571892b7e3a97df057b709321410b1e89a60a` |
| `tests/unit/test_binance_public.py` | 4437 | `013fc266bcf57468fced21d69b987e6e419ba128a8e0a82bcf0716c8a6e39f26` |
| `tests/unit/test_capital_flow.py` | 1636 | `6f22e01cb7d5270c161f09fdab5dfef845b23fa1968cb090e61f4f539d9928af` |
| `tests/unit/test_cli_collect_history.py` | 1774 | `8e0f047f220a535216a165a51242a0ed4413a38214967f78c25087e8fb9dc09d` |
| `tests/unit/test_cli_scan_pipeline.py` | 3691 | `a710cbf4e95d530da009a12f9167e039a7ad3aa7f2a3d2d79cc738b28c905942` |
| `tests/unit/test_data_quality.py` | 1017 | `755dcbf61b56c0510cf20278b9974d94b013609a3de6ef00cbb2d37f3bf5b0d1` |
| `tests/unit/test_evaluation_engine.py` | 5477 | `70774429153b8de9cf707a7b40de076c941b38a9906b88129daef32f97c56b1c` |
| `tests/unit/test_paper_simulator.py` | 3629 | `04e3ec7625b1922515705a4ce900c910258dfc13e587024fcb945c55455226ab` |
| `tests/unit/test_regime_engine.py` | 3429 | `96d76d833d0165ac64b58827c1fc4a9e9dcb0b7776f3ecee5159fa1da3b0c834` |
| `tests/unit/test_regime_signal_gate.py` | 1377 | `924515f85ba2da828f7ced69fbc6ad5632eadce1b5105a54a6cf6a5bfe3af361` |
| `tests/unit/test_runner.py` | 3115 | `eafa32ee5bd17f3b535f002df3c4d69acbcf113a8274922525b2ede9078b560e` |
| `tests/unit/test_scoring_engine.py` | 3254 | `cfad2e38e0d564c1ddcbbe0f2fd9070a2c996be310d3ec9b6d45501d471f5a73` |
| `tests/unit/test_sector_signal_gate.py` | 1200 | `f54a04b39bb9784ad3795c41bf680d19bc3618fc93411411ac0b58ee869cf50e` |
| `tests/unit/test_sector_strength.py` | 2460 | `5e975759d640e9c8996b8ea55d688af4598bca0a03aef98b59860460d0830906` |
| `tests/unit/test_short_signal_engine.py` | 3333 | `c3ab9cc2f5d0035ea3670a959a6aef9b3eb8293d55e727e3c9df4b800adf2061` |
| `tests/unit/test_signal_engine.py` | 3635 | `74431ecb56d56d44abd4b83773b500da26c56bcc2ef4bf28427a79ed783be06b` |
| `tests/unit/test_signal_ranking.py` | 698 | `7e805e382a71eb6944fd27b66ffb529f4ad49ef8b2472ef2ca87b23c6c127b4b` |
| `tests/unit/test_space_engine.py` | 1197 | `23396c465ccee77e9be0c8a4723469d9e8e3894cb1dbb4b0cf29da3981a0e404` |
| `tests/unit/test_strategy_lab.py` | 5310 | `1f91979f6786c4f56021b5b5cd4d96a0c5b62d73d66aa5d077f79e2a8112e9cf` |
| `tests/unit/test_universe.py` | 2325 | `b1b4fbc5bd710b0924ae3d21aa162b5a3796e2d7a1247459488fe8d390a91f3d` |
| `tests/unit/test_walk_forward.py` | 7145 | `9b986379993452f26c9194727516964980e986d9ef37b0f1c11ce72023cde5b5` |
| `EXPORT_MANIFEST.md` | 18841 | `ff8bae0ecdb4b3e5b9f4502529a82d93401e1103e416a78caa438fadb55f26f0` |

## Safety statement

This export does not add or enable API keys, account access, order endpoints, live trading,
or strategy changes. It is a file-for-file source export plus this manifest.
