// SpeechRecognition 결과(continuous + interimResults)를 한 문장으로 합친다.
// PC 크롬은 결과 조각마다 새로 말한 부분만 주지만, 안드로이드 크롬은 앞 조각 내용을 누적해서 준다
// ("주차장" → "주차장 빈자리"). 그대로 이어 붙이면 "주차장 주차장 빈자리"가 된다(11/12 PM 실기).
export function mergeSpeechResults(results) {
  let text = '';
  for (const result of Array.from(results || [])) {
    const piece = String(result?.[0]?.transcript ?? '').trim();
    if (!piece) continue;
    if (piece.startsWith(text)) text = piece;                    // 누적 결과: 교체
    else if (!text.endsWith(piece)) text = `${text} ${piece}`.trim(); // 새 조각: 이어 붙이기
  }
  return text;
}
