"""网页和本机界面共用的布料花型提示词。"""
FABRIC_CATALOG = {
    "tools": [
        {"id": "fabric", "label": "布料花型设计", "instruction": "", "requires_reference": False},
        {"id": "extract", "label": "花型提取／印花提取", "requires_reference": True, "instruction": "从上传的布料、服装或家纺照片中提取印花纹样。只保留原图中的花朵、枝叶、几何纹样或其他印花元素，忠实保留造型、线条与配色。去掉布料底色、经纬布纹、纤维肌理、褶皱、光照阴影、服装轮廓、人物及背景；校正透视，使纹样平整正视。恢复被轻微褶皱干扰的线条，尽量完整提取可见元素，不随意改变花型。输出清晰干净的平面纹样图，要求透明背景 PNG，无文字、水印、边框或产品展示。"},
        {"id": "seamless", "label": "四方连续图", "requires_reference": False, "instruction": "根据参考图或文字设计四方连续图。保留主要纹样、配色与风格，重新排列为一个正方形循环单元；左右边缘、上下边缘必须准确对应续接，四个角衔接自然，重复平铺后没有明显接缝。只输出一个可平铺的单元，不输出四宫格、网格演示、服装效果图或标注。"},
        {"id": "style", "label": "风格转换", "requires_reference": True, "instruction": "以参考图为基础进行艺术风格转换，保留主体结构、图案构成、比例与辨识度，把线条和色彩转为细腻自然的手绘水彩风格。输出正面平视的干净花型原稿，不增加模特、产品展示或无关场景。具体风格可按我下面的补充要求调整。"},
        {"id": "edit", "label": "AI 修图 · 自定义", "requires_reference": True, "instruction": "按我描述的要求修改参考图，保持未要求修改的主体、构图、花型和配色一致，只调整指定部分。修复应自然、细节清楚，不擅自添加文字、水印或其他元素。具体修改要求："},
        {"id": "remove_texture", "label": "AI 去布纹", "requires_reference": True, "instruction": "去除参考图中的经纬纱线、布纹、纤维肌理、褶皱和光照阴影，同时保留印花图案原有轮廓、细节和配色。校正透视与拉伸变形，让图案成为平整正视、色彩均匀的印花原稿。只清理布料造成的干扰，不把花型内部原本的手绘笔触或设计纹理一并抹掉。"},
        {"id": "redraw", "label": "高清重绘", "requires_reference": True, "instruction": "忠实重绘参考图中的花型，保留原有构图、纹样轮廓、配色和元素数量，提高线条清晰度，修复模糊、锯齿与压缩噪点，恢复合理的细节。输出尽可能高分辨率的干净平面原稿，不改变设计、不新增装饰、无文字或水印。"},
        {"id": "expand", "label": "扩图", "requires_reference": True, "instruction": "在参考图周围自然扩展画面，保持原始区域、风格、配色、纹样大小和密度一致，新增内容与原图连续衔接，不重复明显的局部，不出现突兀边界。用于布料花型时只扩展平面花型，不生成服装、模特或产品场景。扩展方向及比例按补充要求执行。"},
        {"id": "similar", "label": "相似花型再设计", "requires_reference": True, "instruction": "参考原图的主题、配色、艺术风格、纹样尺度与密度，重新设计一张协调但有变化的布料花型。保留整体风格，适度改变元素姿态、细节和排列方式，输出单张新设计的平面原稿，无产品展示、文字或水印。"},
        {"id": "remove_rhinestones", "label": "去除烫钻", "requires_reference": True, "instruction": "去除参考图中的烫钻、亮片及其高光反射，修复被遮挡的原有印花线条与色彩，保持主要图案的造型、布局和配色。修补区域与周围自然一致，不生成新的闪光颗粒、金属点或额外装饰。"},
        {"id": "vector_style", "label": "矢量风格重绘 · 图片", "requires_reference": True, "instruction": "把参考图重绘为干净的矢量插画视觉风格：轮廓清晰、曲线平滑、色块干净、颜色层次简洁，忠实保留主要造型与配色。输出单张平面图片，方便后续描摹矢量化；不生成文件界面、格式标签、文字或水印。"},
        {"id": "cutout", "label": "抠图 · 单图", "requires_reference": True, "instruction": "精细提取参考图中的主体或印花元素，移除无关背景，保留主体完整轮廓、细节、边缘与原有色彩；不留背景残片、白边或锯齿。输出透明背景 PNG，不添加阴影、文字、水印或外框。"},
        {"id": "try_on", "label": "服装换装效果", "requires_reference": True, "instruction": "以参考图片中的人物或模特为基础，根据我的服装描述更换穿着，保留人物身份、面部、姿态、身材比例及原有场景。服装结构和布料纹样贴合身体，褶皱、光线与遮挡自然。服装或花型要求："},
        {"id": "hanging", "label": "服装挂拍／立体效果", "requires_reference": True, "instruction": "参考服装或花型图片，生成一张干净专业的服装挂拍或立体商品展示图片。保留服装款式、剪裁、主要配色及纹样，比例准确，面料和光照自然，背景简洁，边缘清楚。具体服装品类、角度及背景要求："},
        {"id": "scene", "label": "参考场景再设计", "requires_reference": True, "instruction": "参考上传图片的构图、场景、光照与整体风格，根据我的要求生成新的场景或商品展示图。画面完整、比例自然，保留需要参考的主要特征；不添加无关文字、水印、界面元素或商标。具体要求："},
    ],
    "layouts": [
        {"id": "four_way", "label": "四方连续 · 无缝平铺", "instruction": "设计可印在布料上的四方连续花型，输出一张正方形循环单元。左右边缘、上下边缘的图案、位置及颜色必须衔接，横向和纵向重复平铺后自然连续，不出现拼接缝。边缘切开的元素要在对侧对应续接。不要画四宫格或多张拼接示意图。"},
        {"id": "horizontal", "label": "二方连续 · 横向", "instruction": "设计布料用横向二方连续花型，输出一张横向循环单元。左右边缘的图案及颜色必须对应衔接，横向重复时自然连续；上下边缘保持完整，不要求纵向循环。"},
        {"id": "vertical", "label": "二方连续 · 纵向", "instruction": "设计布料用纵向二方连续花型，输出一张纵向循环单元。上下边缘的图案及颜色必须对应衔接，纵向重复时自然连续；左右边缘保持完整，不要求横向循环。"},
        {"id": "half_drop", "label": "半落位连续 · 错位排列", "instruction": "设计布料用半落位连续花型，相邻列中的主纹样错开半个排列节距，节奏自然。将错位排列完整收纳到一张矩形循环单元中，使最终单元上下左右都能直接衔接平铺，无明显接缝；不画拼接示意图。"},
        {"id": "placement", "label": "定位花 · 单独图案", "instruction": "设计布料用定位花图案：一个完整的主花型，主次分明、构图完整，周围保留适当留白。主图案不要被边缘裁切，适合放在衣片、围巾或布料指定位置；输出单个完整设计。"},
        {"id": "border", "label": "边饰 · 连续花边", "instruction": "设计布料用连续边饰花型，装饰纹样沿横向形成清楚的花边层次，左右边缘必须能重复衔接，横向平铺自然连续。上下边缘保持整洁，适合布料边缘或裙摆的装饰带。"},
    ],
    "themes": [
        {"id": "ditsy", "label": "小碎花", "instruction": "细小的花朵、花苞与纤细枝叶，元素大小有变化，疏密均衡，清新细腻，适合连衣裙与轻薄布料。"},
        {"id": "large_floral", "label": "大花卉", "instruction": "大朵花卉与舒展枝叶，层次清楚，花型错落排布，优雅且有装饰感，适合服装与家纺。"},
        {"id": "botanical", "label": "植物枝叶", "instruction": "自然舒展的叶片、枝条与小花，植物形态细腻，方向有变化，整体平衡，清新自然。"},
        {"id": "tropical", "label": "热带植物", "instruction": "龟背竹、棕榈叶与热带花卉，舒展而有层次，明快但配色协调，具有夏日气息。"},
        {"id": "geometric", "label": "几何图案", "instruction": "圆形、弧线、三角形和简洁几何块面，线条清晰，排列有节奏，现代而简洁。"},
        {"id": "stripes", "label": "条纹", "instruction": "宽窄搭配的条纹，间距和粗细有清晰规律，线条干净，适合服装印花；不要加入文字或标志。"},
        {"id": "dots", "label": "波点", "instruction": "大小协调的圆形波点，排列规律或轻微错落，整体密度均衡，干净简洁、经典耐看。"},
        {"id": "checks", "label": "格纹", "instruction": "经纬交织感的格纹，线宽和间距有清晰规律，色块边界干净，经典织物风格。"},
        {"id": "kids", "label": "童趣动物", "instruction": "简洁可爱的动物、小星星与小植物，轮廓清楚，造型温和，元素错落但不过密，适合童装与儿童家纺。"},
        {"id": "fruit", "label": "水果花型", "instruction": "柠檬、草莓或其他协调搭配的水果，辅以小叶片和花朵，生动清新，适合夏季服装与餐厨布料。"},
        {"id": "paisley", "label": "佩斯利腰果花", "instruction": "经典佩斯利腰果纹与细密装饰花纹，弧线流畅，主纹样和小纹样搭配，精致有序。"},
        {"id": "abstract", "label": "抽象肌理", "instruction": "抽象笔触、点线与柔和色块，保留绘画感但画面干净，层次自然，适合艺术感布料印花。"},
        {"id": "reference", "label": "参考图再设计", "instruction": "保留参考图的主要纹样、主体造型与配色，提取可用于布料的元素，重新组织排布，保持原图风格与辨识度。"},
    ],
    "palettes": ["沿用参考图配色；无参考图时采用协调自然配色", "奶油色与柔和浅色", "莫兰迪低饱和配色", "蓝白配色", "复古暖色", "黑白配色", "清新绿白配色", "明快鲜艳配色"],
    "base": "输出用于布料印花的平面花型原稿：正面平视、清晰干净、细节完整。只输出设计本身，不显示衣服、模特、布料褶皱、房间或产品展示，不添加文字、标志、水印、尺寸标注及外边框。图案疏密和留白均衡，配色适合印花。",
    "reference": "如附有参考图片，优先保留其主要元素、造型与配色，按所选连续方式重新排布；参考图中的无关背景和展示场景不带入花型原稿。",
}


def make_fabric_prompt(layout="four_way", theme="ditsy", palette=None, tool="fabric"):
    tool_item = next(item for item in FABRIC_CATALOG["tools"] if item["id"] == tool)
    if tool != "fabric":
        return tool_item["instruction"] + "\n\n补充要求（可填写颜色、风格、纹样大小、疏密或处理范围）："
    layout_item = next(item for item in FABRIC_CATALOG["layouts"] if item["id"] == layout)
    theme_item = next(item for item in FABRIC_CATALOG["themes"] if item["id"] == theme)
    palette = palette or FABRIC_CATALOG["palettes"][0]
    return "\n\n".join((layout_item["instruction"], "纹样主题：" + theme_item["instruction"], "配色：" + palette + "。", FABRIC_CATALOG["base"], FABRIC_CATALOG["reference"]))
