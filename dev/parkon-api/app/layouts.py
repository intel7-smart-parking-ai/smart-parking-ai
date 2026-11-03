"""User-supplied PDF layouts. Bay labels are local to each parking lot."""
LAYOUTS = {
    'A': dict(name='개발원주차장', layout_type='basic', capacity=12, bank=6,
              ev={4, 5}, accessible={11}),
    'B': dict(name='부산역주차장', layout_type='angled', capacity=16, bank=8,
              ev={6, 7}, accessible={14, 15}),
    'C': dict(name='북항주차장', layout_type='loop', capacity=12, bank=6,
              ev={4, 5}, accessible={11}),
    'D': dict(name='신세계백화점주차장', layout_type='mall', capacity=64, bank=16,
              ev={48,49,50,51,52,53,54,55}, accessible={44,45,46,47}),
}
