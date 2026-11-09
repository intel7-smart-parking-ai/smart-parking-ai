export default function ToiletMarker({x,y,label='화장실',scale=1}){
 return <g transform={`translate(${x} ${y}) scale(${scale})`} role="img" aria-label={label}>
  <title>{label}</title>
  <rect x="0" y="0" width="78" height="86" rx="6" fill="#fff" stroke="#c5cfdb" strokeWidth="2"/>
  <image href="/icons/toilet.png" x="4" y="4" width="70" height="78" preserveAspectRatio="xMidYMid meet"/>
 </g>;
}
