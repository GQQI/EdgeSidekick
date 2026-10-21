# 你好插件（开发模板）

这是 EdgeSidekick 的**离线插件开发模板**。不访问网络，不需要凭据。

## 如何打包上传

1. 修改 `manifest.json`：把 `id` 改成你自己的（小写字母开头，2–32 位，仅 `a-z0-9-`）。
2. 在 `tools` 里声明工具，并在 `main.py` 实现同名函数。
3. 把整个文件夹压缩成 zip（清单在根目录或唯一顶层文件夹内均可），大小 ≤10MB。
4. 打开「设置 → 插件」，在上传区域选择该 zip。

## 包结构

```
hello-plugin/
  manifest.json
  main.py
  icon.svg
  README.md
```

## 离线插件提示

保持：

```json
"network": [],
"credentials": []
```

安装后会显示「离线可用」，无需填写凭据。完整说明见应用内「查看开发说明」或仓库 `docs/plugins.md`。

## 工具

| 工具 | 说明 |
| --- | --- |
| `greet` | 返回一句问候 |
