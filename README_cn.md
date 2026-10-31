[English](README.md) · [中文](README_cn.md)

# durweg-audit

**[Durweg](https://github.com/musetee/durweg) 的可选插件：拿一次运行去和 agent 自己的会话记录对照，看记录里的每一步是不是真的通过 `durweg` 命令做的，每次确认时记下的话是不是真的是你说的。**

`durweg check` 检查的是运行自己的记录；durweg-audit 再往前一步，把这份记录和 agent（Claude Code 或 Codex）在会话里实际做的事对照。一次运行很重要、你想确定 agent 没有跳步、造假或改记录时用它。

## 🧭 安装

需要先装好 Durweg，电脑上要有 git。

1. **打开终端**（和 Durweg 的“每天怎么用”第 1 步一样）。
2. **下载：**
   - macOS、Linux、WSL：`git clone https://github.com/musetee/durweg-audit.git ~/projects/durweg-audit`
   - Windows（Durweg 装在 Windows 本机上），在 PowerShell 里：
     `git clone https://github.com/musetee/durweg-audit.git "$HOME\projects\durweg-audit"`

`~` 和 `$HOME` 都是你的家目录（Windows 上是 `C:\Users\<你>`）。

## 📅 怎么用

1. **找到这次运行对应的 agent 会话文件：**

   | agent | 会话文件在哪里 |
   | :--- | :--- |
   | Claude Code | `~/.claude/projects/<项目文件夹路径，把每个 /（Windows 上还有 \ 和 :）换成 ->/`（每个会话一个 `.jsonl` 文件） |
   | Codex | `~/.codex/sessions/<年>/<月>/<日>/`（每个会话一个 `rollout-….jsonl` 文件） |

   Windows 上它们在 `C:\Users\<你>\.claude\projects\` 和 `C:\Users\<你>\.codex\sessions\` 下面。

2. **运行：**
   - macOS、Linux、WSL：
     `python3 ~/projects/durweg-audit/bin/durweg-audit <运行文件夹> --transcript <会话文件> [更多会话文件]`
   - Windows，在 PowerShell 里（`py -3` 是 Python 启动器；没有的话用 `python`）：
     `py -3 "$HOME\projects\durweg-audit\bin\durweg-audit" <运行文件夹> --transcript <会话文件> [更多会话文件]`

第一行就是结果：

| 结果 | 退出码 | 意思 |
| :--- | :---: | :--- |
| `PASS` | 0 | 每一步、每次确认都能在会话里找到依据 |
| `VIOLATION` | 1 | 记录里有和会话对不上的地方（逐条列出） |
| `INSUFFICIENT` | 2 | • 有些事光看会话判断不了（列为 `gap G-…`）<br>• 逐条看一下，把你核对的结果写在 `<运行>/audit-notes.md` 的 `## G-…` 下面，audit 就算它核对过了 |
| 设置问题 | 4 | 找不到 Durweg 或某个文件 |

它只读一组固定格式的 shell 命令；读不了的命令不会被当作依据，只会列为 gap。

## 🙋 我想……

| 我想 | 怎么做 |
| :--- | :--- |
| 配合没有正式安装的 Durweg（一份源码）用 | 把 `DURWEG_PLATFORM` 设成那个文件夹；不设的话，audit 通过 `~/Durweg/.install.json` 找 Durweg |
| 跑它的测试 | `python3 -m unittest discover -s tests`（需要装好 Durweg，或设置 `DURWEG_PLATFORM`） |

支持 Durweg 1.x。

---

MIT 许可证：[LICENSE](LICENSE)。
