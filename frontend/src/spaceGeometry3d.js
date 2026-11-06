// Coordinates follow the existing 2D drawings, including each lot's zone order.
export function spaceGeometry3d(lot){
 const sizes={basic:[1000,1040],angled:[1200,760],loop:[1000,880],l_shape:[900,1100],mall:[1536,1024]};
 const size=sizes[lot.layout_type];if(!size)return null;
 const counts={};
 const spaces=lot.spaces.map(space=>{
  const zone=space.zone,i=counts[zone]||0;counts[zone]=i+1;
  let x,y,w,h,rotation=0,slant=0;
  if(lot.layout_type==='basic'){x=zone==='A'?60:640;y=145+i*125;w=300;h=125;rotation=90;}
  if(lot.layout_type==='angled'){x=65+i*124;y=zone==='A'?125:465;w=199;h=165;rotation=24.44;slant=75;}
  if(lot.layout_type==='loop'){x=185+i*105;y=zone==='A'?215:440;w=105;h=215;}
  if(lot.layout_type==='l_shape'){x=zone==='A'?60+i*112:650;y=zone==='A'?100:455+i*110;w=zone==='A'?112:185;h=zone==='A'?220:110;rotation=zone==='A'?0:90;}
  if(lot.layout_type==='mall'){const origin={A:[119,134],B:[822,134],C:[119,570],D:[822,570]}[zone];if(!origin)return null;const row=Math.floor(i/8);x=origin[0]+i%8*75;y=origin[1]+row*(['A','B'].includes(zone)?191:182);w=75;h=row?120:132;}
  return {...space,x,y,w,h,rotation,slant};
 }).filter(Boolean);
 return {width:size[0],height:size[1],spaces};
}
