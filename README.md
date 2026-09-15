# SHU Notes

个人的上海大学课程笔记。

## 目录结构

- 课程笔记：`学科/科目/notes/`，例如 `mathematics/mathematical-analysis-i/notes/`。
- 文件命名：`章号-节号-英文主题`，同目录保存 `.tex` 和 `.pdf`。
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
