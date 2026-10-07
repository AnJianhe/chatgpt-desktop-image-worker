# 0.2.0 验证记录 — 2026-10-07

本记录属于离线和模拟 HTTP 验证，没有操作真实 ChatGPT、虚拟机、鼠标或键盘，也没有验证实际生成时长。

- Python：125 项测试通过，包括旧完成标记、滚到底部、原图下载、剪贴板、队列、隧道和新工作流测试。
- 生产运行版：同样的新工作流 9 项测试通过。
- 独立 HTTP 检查：配置、容量、串行执行、幂等、结果文件、失败恢复、历史与关闭均通过。
- 固定入口：原有 5 项 Node 测试通过，没有更改入口部署。
- Edge 无界面浏览器：用模拟 HTTP 实际执行网页脚本，验证自定义原文、多图、删除重加、连续提交、确认操作、任务选择隔离、刷新恢复、移动布局；无 JavaScript 异常。

## 需求对应证据

| 用户检查项 | 验证方法 |
|---|---|
| 1～2 无图与条件提示词 | required_and_optional_tools / conditionals_are_resolved_before_submission；检查实际提交给 runner 的条件分支，不声称 AI 必定生成 |
| 3～6 上传 1～3 张与第4张限制 | custom_exact_characters_zero_to_three_images / fourth_image_duplicate_and_invalid_list_rejected；Edge 多图交互 |
| 7 独立删除重新添加 | Edge 删除中间图片并重新添加，再提交新任务；先前任务仍保留原来的3个ID |
| 8 必須图片的工具 | 遍历全部 requires_reference 工具，缺图 400，有图 202 |
| 9～11 纯自定义与0～3张 | 精确比较空白、换行与业务文字；后端发送内容等于用户原文 |
| 12～14 连续任务与隔离排队 | Edge A/B/C 不锁表单；后端阻塞 A 验证 B/C queued；图片私有副本不受原上传文件删除影响 |
| 15～17 60秒与已经生成/继续等待 | 原有 sixty_second_review / remote_review / bottom_completion 测试；Edge 三按钮处理 |
| 18～19 二次确认与取消 | Edge 首次点击只打开 dialog，取消不发送任何 review 请求且任务状态不变 |
| 20～23 not_generated与释放 | 后端拒绝缺确认/错误截图版本；确认结束不下载、不返回结果，B/C继续执行，不自动重提A |
| 24 单任务失败隔离 | A结束，B模拟异常 error，C仍done |
| 25 生成下载保存回归 | 原有 automation / upload_support / check_server 离线测试 |

底层只有一个桌面 worker。支持边执行边准备/提交后续任务，没有实现同一个 ChatGPT 窗口的并行生成；不能保证两张图和一张图耗时相同。
任务仍保存在服务器内存中，部署更新前应等待现有任务结束。网页恢复未上传的文件会明确提示重新选择，不会悄悄去掉参考图。
