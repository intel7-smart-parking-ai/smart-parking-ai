// Place labels inside the bay's actual outline, including slanted bays.
export function bayGeometry(points) {
  const vertices = points.trim().split(/\s+/).map(point => point.split(',').map(Number));
  const top = Math.min(...vertices.map(([, y]) => y));
  const bottom = Math.max(...vertices.map(([, y]) => y));
  function span(y) {
    const intersections = [];
    vertices.forEach(([x1, y1], i) => {
      const [x2, y2] = vertices[(i + 1) % vertices.length];
      if (y1 !== y2 && y >= Math.min(y1, y2) && y <= Math.max(y1, y2)) {
        intersections.push(x1 + (y - y1) * (x2 - x1) / (y2 - y1));
      }
    });
    const left = Math.min(...intersections), right = Math.max(...intersections);
    return {x: (left + right) / 2, width: right - left};
  }
  const height = bottom - top;
  const middle = span(top + height / 2);
  const floorSymbolSkew = Math.atan2(span(bottom).x - span(top).x, height) * 180 / Math.PI;
  const labelSize = Math.min(26, Math.max(14, middle.width * .2));
  const labelY = top + labelSize / 2 + 10;
  const stopY = bottom - 13;
  const stop = span(stopY);
  return {
    labelX: span(labelY).x, labelY, labelSize,
    centerX: middle.x, centerY: top + height / 2,
    floorSymbolSkew,
    floorSymbolScale: Math.max(0, Math.min(middle.width * .68 / 110, (height - labelSize - 34) / 100)),
    statusSize: Math.min(17, Math.max(10, middle.width * .14)),
    specialX: span(bottom - 32).x, specialY: bottom - 32,
    specialSize: Math.min(16, Math.max(9, middle.width * .13)),
    carScale: Math.min(1, height / 200, middle.width / 110),
    stopPath: `M${stop.x - stop.width * .2} ${stopY}h${stop.width * .4}`,
  };
}
