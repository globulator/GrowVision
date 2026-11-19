def calculate_area_display(
    area_px: float | None,
    aruco_area_px: float | None,
    aruco_size_mm: float | None,
) -> str | None:
    """Рассчитывает отображаемое значение площади растения по DB_SCHEMA.md п. 4.5.

    - При наличии маркера и размера маркера: '{area_cm2} см²'
    - Без маркера или без размера: '{int(area_px)} px'
    - При отсутствии замера: None
    """
    if area_px is None:
        return None

    if (
        aruco_area_px is not None
        and aruco_size_mm is not None
        and aruco_area_px > 0
        and aruco_size_mm > 0
    ):
        scale = (aruco_size_mm / 10.0) ** 2
        area_cm2 = round(area_px * scale / aruco_area_px, 1)
        return f"{area_cm2} см²"

    return f"{int(area_px)} px"
