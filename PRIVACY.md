# Privacy

This plugin evaluates decision tables inside the Dify plugin process.

- It makes no network requests and uses no credentials.
- It does not store anything: the table and the inputs of each call are used for that call only.
- Its outputs (the evaluation envelope, including the inputs it used and the trace) go back to the calling Dify workflow, like any tool output.

隐私说明：本插件只在 Dify 插件进程内执行决策表，不发起任何网络请求，不使用凭据，不存储数据；每次调用的决策表与输入只用于该次调用，结果（含所用输入与轨迹）返回给调用它的 Dify 工作流。
