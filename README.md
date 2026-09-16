# SHU Notes

个人的上海大学课程笔记。

## 目录结构

- 课程笔记：`学科/科目/notes/`，例如 `mathematics/mathematical-analysis-i/notes/`。
- 文件命名：`章号-节号-英文主题`，同目录保存 `.tex` 和 `.pdf`。
- 课后作业：放在对应节笔记末尾的“课后作业”模块，按教材题号组织；没有课堂笔记时，该节只保留作业。每题附题干及完整、严谨的解答。
- `template.tex`：通用笔记模板。
- `personal-textbook/`：自用教材；`misc/`：未归类内容。

## 编译

使用 **LuaLaTeX**，在源文件所在目录运行：

```sh
lualatex filename.tex
```

含目录或交叉引用时运行两次。

## 博客同步

在 LaTeX 文件顶部的 `halo` 注释块中配置文章标题、分类和标签，推送到 `main` 后由 GitHub Actions 同步至 Halo。首次使用需配置 `HALO_TOKEN`，见 [发布说明](publish/README.md)。

## 通识课程笔记

- [第一讲：数据之用：从数据到模型](general-education/data-and-optimization/notes/01-01-data-and-modeling.pdf)（[LaTeX 源文件](general-education/data-and-optimization/notes/01-01-data-and-modeling.tex)）。课程目录暂用描述性名称“数据与优化”，编号按讲次整理；仅保存在仓库，`publish: false`，不发布博客。
