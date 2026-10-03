# oss-pr-recon

[English](README.md) | 简体中文

一个 Claude Code Skill 加四个小脚本，只回答一个问题：**这个开源贡献目标真的没人做吗？提了 PR 能活下来吗？** 在写代码之前回答它。

在热门仓库（1 万星以上、每周几十个合并）里，大多数被拒的 PR 不是因为代码质量差，而是因为：issue 早被别人认领了；另一个开着的 PR 正在改同一个函数；仓库一个 issue 只保留一个 PR；或者漏了 AI 披露、发版说明这类规定。这个 Skill 按顺序逐项检查。

## 内容

```
skills/oss-contribution-recon/
├── SKILL.md            方法：规则 → issue → 文件 → 代码段 → 仓库治理规则 → 通过概率
├── scripts/            需要算准的数交给脚本，不每次手写（也就不会每次写错）
└── repos/              每个仓库一份规则清单，标注核对日期和来源
```

| 脚本 | 回答什么 |
|------|---------|
| `issue_prs.py owner/repo N` | 引用过 issue N 的所有 PR（任何状态），以及每个被关 PR 的最后一条评论。`--diff-match 正则` 只统计做了某类改动的 PR。 |
| `contention_map.py owner/repo --build` / `--check 路径 --line 行号` | 每个开着的 PR 改了哪些文件、哪些行；你打算改的地方是硬冲突、热文件还是互不相干。 |
| `base_rate.py owner/repo` | 最近 N 天外部贡献者 PR 的合并率，以及被关的那些是怎么死的。 |
| `pr_status.py` | 你在别人仓库里开着的 PR：CI、审阅、新评论，以及 12 个月内的合并数。 |

为什么用脚本而不是文字说明：下面这些测量手写时每一个都出过错。

- 搜索接口的 `closed:` 日期过滤会漏掉大量"关闭未合并"的 PR。同一仓库同 30 天，搜索得到 261 合并、25 关闭（91%），REST 列表是 261 和 113（69.8%）。
- `author_association` 会把组织成员身份不公开的员工标成 `CONTRIBUTOR`，有些项目机器人也是普通用户账号。同一仓库里，这让外部贡献者合并率从 48.8% 变成了 65.5%。
- `gh pr view --json files` 最多返回 100 个文件，一个改了 234 个文件、也碰了你的文件的重构 PR 只显示 100 个。
- 一个大 issue 底下的合并率，如果合并的 PR 和你做的不是同一类改动，对你没有参考意义。

## 安装

作为 Claude Code 插件：

```
/plugin marketplace add ShousenZHANG/oss-pr-recon
/plugin install oss-pr-recon@oss-pr-recon
```

或者把 `skills/oss-contribution-recon/` 复制到 `~/.claude/skills/`。

需要 Python 3.10+ 和已登录的 [GitHub CLI](https://cli.github.com/)（`gh auth status`）。不需要安装 Python 包。

## 使用

让 Claude 挑选或核查目标（"owner/repo 的 issue 1234 能做吗？"），Skill 会自动加载。脚本也能单独跑，命令见 [README.md](README.md#use)。

## 原则

- 这里没有任何东西会发评论、开 PR，只读。
- 各仓库自己的规定优先：AI 披露写法、同时开 PR 的上限、标题格式都不一样。`repos/` 里的文件记录的是标注日期当时的情况，旧的要重新核对。
- 目的是少提、提准，不是多提。

## 致谢

- 设计思路借鉴 [alibaba/open-code-review](https://github.com/alibaba/open-code-review)：不能出错的步骤用确定性代码完成，规则按文件路径匹配。没有复制它的代码或提示词。
- 想要端到端贡献流程，可搭配 [majiayu000/spellbook](https://github.com/majiayu000/spellbook)（MIT）里的 `contributor`。

## 许可证

[MIT](LICENSE)
