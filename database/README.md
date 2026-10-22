# 本地知识库目录

这个目录是运行时数据区。Git 仓库只保留目录结构和本说明，**不包含任何真实知识库内容**。

```text
database/
  pdfs/          PDF 文档
  html/          HTML 文件
  urls/          URL 记录（urls.jsonl）
  images/        图片
  screenshots/   截图
  passwords/     DPAPI 加密密码库
  markdown/      Markdown 笔记
  code/          代码片段
  scripts/       脚本
  inbox/         待整理文件
  index.sqlite3  SQLite FTS5 + 向量索引（运行时生成，已忽略）
  catalog.json   文件目录（运行时生成，已忽略）
  INDEX.md       可读目录（运行时生成，已忽略）
```

真实文件由 `KnowledgeBase` / `LibraryOrganizer` 在运行时创建；`.gitignore` 会忽略
这些目录中的所有数据，只允许 `.gitkeep` 和本说明进入 Git。
