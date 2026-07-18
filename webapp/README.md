# AzurPilot Webapp

这里保留的是 AzurPilot 的纯 Web 前端资源，不再包含桌面壳或桌面打包流程。

## 常用命令

```bash
pnpm install
pnpm run watch
pnpm run build
pnpm run typecheck
pnpm run lint
```

`packages/renderer` 是 Vue 3 + Vite 应用，默认通过 iframe 访问本地 WebUI：`http://127.0.0.1:22267`。如需覆盖地址，可在环境变量中设置 `VITE_WEBUI_URL`。
