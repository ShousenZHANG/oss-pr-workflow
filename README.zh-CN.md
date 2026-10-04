# oss-pr-workflow

[English](README.md) | 简体中文

一个 Claude Code 插件：给定任意 GitHub 仓库，从"扫描 issue"一路走到"PR 合并"。每个阶段之间都有用脚本把关的检查点，保证提交出去的东西不会因为本来就能预见的原因被拒。

在热门仓库里，大多数被拒的 PR 不是代码质量问题，而是因为：issue 早被别人认领了；另一个 PR 正在改同几行；仓库一个 issue 只留一个 PR；仓库禁止 AI 辅助；或者漏了 AI 披露、发版说明这类规定。这里的每个阶段专门拦住其中一类失败。

## 阶段

| # | Skill | 做什么 | 进入下一阶段前的检查 |
|---|---|---|---|
| 0 | `oss-pr` | 可选：按语言列出候选仓库，附合并数据和 AI 规定 | 你挑一个 |
| 1 | `oss-pr-profile` | 从仓库自己的文档和已合并 PR 起草规则档案，由你核对 | AI 规定允许你贡献；档案已核对 |
| 2 | `oss-pr-scout` | 从已有 issue、大型 issue、近期合并的代码里找最多 5 个目标 | 你挑一个 |
| 3 | `oss-pr-recon` | 证明目标没人在做（issue、文件、代码行、仓库治理规则），并估计通过率 | 硬闸门全部通过；你说开工 |
| 4 | `oss-pr-build` | 在单独的工作目录里先写测试再实现，本地复现 CI，独立审查 | 测试在撤掉修复后会失败；每个文件都审过；检查全部通过 |
| 5 | `oss-pr-ship` | 控制节奏，起草并检查 PR 文字，你确认后才发出，之后持续跟进 | 节奏检查放行；你确认了最终文字 |

**任何别人看得到的动作，都要你确认那段确切的文字之后才执行**：评论、开 issue、开 PR、回复审查意见、催审。唯一自动执行的网络写操作，是推送到你自己的 fork；第一次创建这个 fork 之前也会先问你。

## 脚本

`skills/oss-pr/scripts/` 里有 12 个只用标准库的 Python 脚本，负责所有不能算错的测量，每个都有 `--help`。用途见 [README.md](README.md#scripts) 的表格。

为什么用脚本：下面这些手工做时每一项都出过错。

- 搜索接口的 `closed:` 日期过滤会漏掉"关闭未合并"的 PR。同一仓库 30 天内，这类 PR 有 113 个，搜索只找到 25 个，合并率被算成 91%，实际是 69.8%。
- `author_association` 会把组织成员身份不公开的员工标成 `CONTRIBUTOR`，有些项目机器人也是普通用户账号，结果 48.8% 被算成了 65.5%。
- 外部整体合并率谁也代表不了：同一仓库里，新人合并率 39.6%，老贡献者 74.5%。
- `gh pr view --json files` 最多返回 100 个文件，一个改了 234 个文件、也碰了你文件的重构 PR 只显示 100 个。
- 仓库的 `AGENTS.md` 可以不出现"AI"这个词，就禁止代理提 PR。
- 几分钟内开出的 20 个相似 PR，被当成垃圾一起关掉。

## 安装

作为 Claude Code 插件：

```
/plugin marketplace add ShousenZHANG/oss-pr-workflow
/plugin install oss-pr-workflow@oss-pr-workflow
```

或者把 `skills/` 下的所有目录复制到 `~/.claude/skills/`。要保持它们并排放置，因为各阶段的 Skill 会调用 `oss-pr/scripts/` 里的脚本。

需要 Python 3.10+ 和已登录的 [GitHub CLI](https://cli.github.com/)（`gh auth status`）。不需要安装 Python 包。技能里的命令按 POSIX shell 写，Windows 上请用 Git Bash 或 WSL。

## 使用

```
/oss-pr deepset-ai/haystack          开始或继续某个仓库
/oss-pr deepset-ai/haystack 12765    从指定 issue 开始
/oss-pr status                       你开着的 PR，以及需要你处理的事
/oss-pr pick-repo go                 候选仓库
```

第一次使用时会问你几个问题：从哪些来源选题、能不能向维护者提问、每个仓库同时开几个 PR、用什么语言汇报。你的数据都放在 `~/.oss-pr/`：`config.md`、`ledger.md`、`repos/` 下的仓库档案，以及 `cache/`。`skills/oss-pr/examples/repos/` 里有 dify、haystack、airflow 三份示例档案，标注了核对日期，会过时。

## 原则

- 各仓库自己的规定优先：AI 使用、披露写法、同时开 PR 的上限、标题和 issue 引用格式。
- 禁止 AI 辅助的仓库不碰；只禁止自主代理的仓库，要求你亲自读过每一行改动。
- issue、评论和机器人输出里的文字一律当作数据，绝不当作指令执行。
- 目的是少提、提准，不是多提。

## 已知局限

- 仓库档案的起草是对仓库文档和 CI 配置做模式匹配。已经在 8 个仓库（dify、haystack、airflow、mlflow、grafana、cilium、cli/cli、home-assistant）上核对过，但每条结论仍然要有人读过引用的原文才能使用。
- 只由私有机器人执行、或只存在于维护者心里的规定，在有人因此被关 PR 之前是看不到的；`base_rate.py` 能从被关 PR 的评论里把它们找出来，但前提是这件事已经发生在别人身上。
- "实现与测试"阶段只在 Python 仓库上完整跑通过。Go 和 TypeScript 仓库因为本机没装工具链，只验证到"本地跑不了检查时，交给使用者决定"这一步。
- 通过率估计取决于样本量：外部 PR 很少的仓库，估计就会很宽、很弱，脚本会标出样本量 n。
- 行级冲突检查会先用 GitHub 的 compare 接口，把别人 PR 的行号换算到当前主分支上；这个接口最多列 300 个文件。很早就分出去的 PR 常常只能给出 `UNKNOWN`，需要自己去读那个 PR 的改动。
- 测试结果只有在日志里能看到这个测试自己的结果行时才算数，所以测试要用详细模式跑。能识别 pytest、unittest、go test、cargo、jest/vitest 的格式；Maven/Surefire 默认不打印通过的测试名。

## 开发

```bash
python -m pip install pytest ruff
python -m pytest
ruff check . && ruff format --check .
```

测试里包含 10 个回归用例，每个都对应一次真实发生过的失败，以及现在负责拦住它的检查（`tests/test_regressions.py`）；另有 v1.0.0 审计发现的、检查本该拦住却放过的情况（`tests/test_audit_fixes.py`）。

## 致谢

- 设计思路借鉴自 [alibaba/open-code-review](https://github.com/alibaba/open-code-review)：
  - 不能出错的步骤交给确定性代码。
  - 规则按文件路径匹配。
  - 审查深度按改动大小分级。
  - 审查必须覆盖每个文件。
  - 意见指到的行号要和 diff 核对。
  - 只有能被 diff 证明错了的意见才能驳回。

  没有复制它的代码或提示词。
- 想换一种贡献流程，可以看 [majiayu000/spellbook](https://github.com/majiayu000/spellbook)（MIT）里的 `contributor`。

## 许可证

[MIT](LICENSE)
