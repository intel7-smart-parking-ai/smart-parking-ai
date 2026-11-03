"""User-supplied PDF layouts. Bay labels are local to each parking lot."""
LAYOUTS = {
    'A': dict(name='1번 주차장 · 기본형', layout_type='basic', capacity=12, bank=6,
              ev={4, 5}, accessible={11}),
    'B': dict(name='2번 주차장 · 사선형', layout_type='angled', capacity=16, bank=8,
              ev={6, 7}, accessible={14, 15}),
    'C': dict(name='3번 주차장 · 일방순환형', layout_type='loop', capacity=12, bank=6,
              ev={4, 5}, accessible={11}),
    'D': dict(name='4번 주차장 · L자복합형', layout_type='l_shape', capacity=10, bank=5,
              ev={3, 4}, accessible={9}),
}
