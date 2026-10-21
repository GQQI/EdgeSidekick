# 上传自定义插件

把插件文件夹打成 zip（≤10MB），在「设置 → 插件」上传。zip 根目录（或唯一顶层文件夹）必须包含 `manifest.json`。

## 包结构

```
my-plugin/
  manifest.json   必需
  main.py         必需（或 manifest.entry 指定的文件）
  icon.svg        可选
  README.md       可选
```

## 离线插件

不访问外网、不需要凭据时，把 `network` 和 `credentials` 都设为空数组：

```json
"network": [],
"credentials": []
```

这类插件安装后即可用，市场会标「离线可用」。

## 快速开始

1. 在插件页点「下载开发模板」，得到 `hello-plugin` 示例。
2. 改 `manifest.json` 的 `id` / `name` / `tools`，在 `main.py` 实现同名函数。
3. 压缩文件夹为 zip 后上传。

完整开发说明见 [plugin-dev-guide.md](plugin-dev-guide.md)；字段与 API 见 [plugins.md](plugins.md)。也可在应用内点「查看开发说明」。
