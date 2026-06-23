# 📑 Tab Board

实时 Chrome 标签页看板 —— 把挤成一行、看不清的几十个 tab，自动按语义归类成一屏可读的分组面板。点标题直接跳到那个真实的 tab，悬停可关闭，一键去重，整组关闭。

> 仅支持 **macOS + Google Chrome**（通过 AppleScript 读取/控制 Chrome）。纯 Python 标准库，无第三方依赖。

![demo](docs/demo.png)

## ✨ 功能

- **实时更新**：每 2 秒读取当前 Chrome 全部窗口/标签，开关任何 tab 自动反映（无变化不重绘，不闪烁）
- **自动归类**：按关键词把 tab 分到 1:1 笔记 / 会议 OKR / 文档 / 学习课程 / 个人 等类别，认不出的进「其他」
- **点标题 = 跳真实 tab**：激活并置顶 Chrome 里那个已打开的标签，而不是新开一个
- **关闭单个 / 整组 / 一键去重**：直接操作真实标签

## 🚀 安装

推荐用 [pipx](https://pipx.pypa.io)（把命令行工具装进隔离环境，全局可用）：

```bash
# 没装 pipx 的话先装：
brew install pipx && pipx ensurepath

# 从 GitHub 一条命令安装：
pipx install git+https://github.com/<你的用户名>/tabboard.git
```

或用 pip 装到当前环境：

```bash
pip install git+https://github.com/<你的用户名>/tabboard.git
```

## 使用

```bash
tabboard
```

会启动本地服务并自动打开浏览器 `http://localhost:8765`。`Ctrl+C` 停止。

## 🔧 自定义分类

编辑 `tab_board.py` 里的 `RULES`（顺序即优先级，命中 title+url 关键词即归类）和 `CATS`（emoji + 名字 + 顺序）。改完重新安装即可。

## License

MIT
