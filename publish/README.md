# Astro 课程笔记自动发布

推送到 `main` 后，**Publish notes to Astro** 将已启用的 LaTeX 笔记转换为 HTML／MathML，放入 Astro 的独立 `notes` 集合，再构建、更新搜索索引并发布到现有服务器。也可以在 Actions 手动运行。

## 日常使用

1. 在笔记导言区维护原有 `% halo:begin`／`% halo:end` JSON 注释块。这两个标记为兼容已有笔记继续使用，发布流程已不依赖 Halo。
2. 将需要发布的笔记设为 `"publish": true`，提交对应 `.tex` 和检查过的同名 `.pdf`。
3. 推送到 `main`，在 Actions 查看构建与发布结果。

标题、课程、节号、摘要、分类和标签继续使用现有 metadata；不自动改写正文或生成摘要。文件名采用 `01-02-<english-topic>.tex`，`section` 使用 `1.2`。`course`、`categories`、`tags` 必须登记在 [notes.json](notes.json)。

| 课程键 | Astro 路径 |
| --- | --- |
| `mathematical-analysis-i` | `/notes/mathematical-analysis/<文件名>` |
| `higher-algebra-i` | `/notes/higher-algebra/<文件名>` |

新增课程还需在博客的 `src/lib/courses.ts` 登记。课程笔记不进入普通博客文章列表或 RSS。

`publish: false` 和无 metadata 的笔记不导出；停止同步不会自动删除已上线内容。`excluded_slugs` 保留明确删除的文章排除项，防止同步时重新出现。发布只覆盖本次导出的笔记，保留其他课程内容与普通博客文章。

## 转换与发布

Pandoc 3.9 保留现有章节、定理、证明和解的编号及 MathML。已登记的 TikZ 图使用校验过的 SVG，遇到不支持的图或缺失 PDF 会停止转换。PDF 链接继续指向仓库 `main` 的同名文件。

导出目录包含 `notes/<课程路径>/<文件名>.md` 和带 SHA256 的 `manifest.json`。首次发布日期与更新时间取自 Git 历史，因此 workflow 使用完整 checkout。

Actions 从服务器取得私有博客源码快照，覆盖导出笔记，在 runner 上使用锁定的 Bun 版本和依赖构建。接收端核验文件、manifest、源码版本与构建页面，再原子切换静态站和私有源码快照，保留上一版本。源码在构建期间发生变化会拒绝发布，需要重新构建。

普通文章或主题另行更新时，也应同步服务器上的私有源码快照；本流程只更新课程笔记。

## 服务端与 Secrets

接收端 `scripts/astro_receiver.py` 由管理员安装到 `/opt/xy0v0-blog/receiver.py`；私有源码快照由 `/opt/xy0v0-blog/state/source` 指向。专用 `astro-publish` 账号的 SSH key 只能执行接收端的 `source` 和 `publish` 两项操作，不能执行任意 shell 命令。

仓库 Actions Secrets：

- `ASTRO_SSH_HOST`：博客服务器地址。
- `ASTRO_SSH_USER`：专用发布账号。
- `ASTRO_SSH_PRIVATE_KEY`：该账号的受限发布密钥。
- `ASTRO_SSH_KNOWN_HOSTS`：经现有可信连接核对的服务器公钥，客户端强制校验。

流程不再读取 `HALO_TOKEN`。博客源码、私钥和下载的源码快照不上传为公开 Actions artifact。

## 本地检查

安装 Pandoc 3.9，在仓库根目录运行：

```sh
python3 -m unittest discover -s tests
python3 scripts/publish_notes.py --preview
python3 scripts/publish_notes.py --export-astro build/astro
```

预览输出到 `build/astro-preview/`。`scripts/deploy_astro.py prepare` 与 `publish` 用于 workflow，需要上述 SSH 环境变量；本地转换不需要凭据。
