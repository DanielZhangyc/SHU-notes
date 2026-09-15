# Halo 自动发布

## 发布内容

每篇笔记在 LaTeX 导言区保存一个 JSON 注释块，PDF 中不会渲染：

```tex
% halo:begin
% {
%   "version": 1,
%   "publish": true,
%   "course": "mathematical-analysis-i",
%   "section": "1.2",
%   "title": "数学分析（一）1.2：数集与确界原理",
%   "categories": ["study-notes"],
%   "tags": ["mathematics"],
%   "excerpt": "邻域、数集的有界性、上下确界与确界原理，以及相关例题和证明。"
% }
% halo:end
```

当前文章标题为 **数学分析（一）1.2：数集与确界原理**，分类为 **学习笔记**，标签为 **数学**。PDF 链接指向 GitHub `main` 分支中的同名 PDF。

### 格式约定

- 所有示例字段均为必填，字段名与版本固定；重复字段、多余字段及格式错误会导致检查失败。
- `publish` 使用 JSON 布尔值。模板默认 `false`；改成 `true` 后纳入同步。没有 metadata 的笔记跳过，已有 metadata 即使未启用发布也要通过内容格式检查。
- `course`、`categories`、`tags` 使用 [notes.json](notes.json) 中登记的英文键。分类恰好一个；标签至少一个且唯一，发布时按键排序。
- `section` 采用 `1.2` 格式。已启用笔记的路径必须匹配课程目录，文件名必须为 `01-02-<english-topic>.tex`。
- `title` 必须精确等于 **课程显示名 + 节号 + 中文冒号 `：` + LaTeX 的纯文本 `\title`**，如上例。标题中的空格、标点和课程名按这一规则统一。
- `excerpt` 为非空单行摘要。metadata 放在导言区，每一行保留 `% ` 前缀。
- slug 自动生成，例如 `mathematical-analysis-i-01-02-sets-and-suprema`；已发布文件保持原路径，以便更新同一篇文章。

`notes.json` 仅保存站点配置、课程登记和分类／标签词表。新增分类或标签时，先在 Halo 创建，再登记对应键与准确的显示名称；脚本只匹配已有项。当前令牌只需“允许投稿”及“允许发布自己的文章”，分类／标签查看由依赖权限提供。

设为 `publish: false` 表示停止后续同步，已发布文章仍保留在 Halo。所有笔记的 metadata 与正文转换通过检查后才开始远端写入。

## 启用

1. 将完整、有效的 Halo 个人令牌添加到 GitHub 仓库的 **Settings → Secrets and variables → Actions → New repository secret**，名称为 `HALO_TOKEN`。
2. 将 workflow、脚本、测试、站点配置和笔记提交到 `main`。
3. 在 Actions 中手动运行 **Publish notes to Halo**，或推送受监听的文件来自动触发。

workflow 监听 `main` 中笔记 `.tex`／`.pdf`、站点配置、发布脚本、测试及自身的变更。脚本扫描 `学科/科目/notes/*.tex`，每次同步其中 `publish: true` 的全部文章。

## 实现

Pandoc 3.9 读取 LaTeX，脚本恢复章节与定理编号、中文“证明”标记，输出 HTML 和原生 MathML。Halo 保存生成的 HTML，Astro Pure 主题直接显示正文；这套路径无需额外的公式脚本。最终线上效果仍需在真实文章页核验。

脚本使用 Halo 2.25.4 的 UC API 创建／更新本人文章和草稿，再调用发布接口。固定的 `notes-<slug>` 标识与源文件标记用于识别同一篇文章；重跑会更新已有文章，内容未变时跳过写入。更新基于服务器返回的版本号，保留发布时间及其他非配置字段。

PDF 复用仓库中已检查的文件，修改笔记时将 `.tex` 与重新编译的 `.pdf` 一起提交。凭据只从环境变量读取。

## 本地检查

安装 Pandoc 3.9 后，在仓库根目录运行：

```sh
python3 scripts/publish_notes.py --preview
python3 -m unittest discover -s tests
```

预览位于 `build/halo/`。设置 `HALO_TOKEN` 后，`--check` 只读核查认证、分类、标签和文章对应关系；`--publish` 才会实际发布。

依据：[Halo UC API 源码](https://github.com/halo-dev/halo/blob/v2.25.4/application/src/main/java/run/halo/app/core/endpoint/uc/UcPostEndpoint.java)、[Pandoc 文档](https://pandoc.org/MANUAL.html)。
