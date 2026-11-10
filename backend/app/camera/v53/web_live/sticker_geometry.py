"""Rectify a detected square label; color remains the Geti model's observation."""
import cv2
import numpy as np


def rectify_sticker(crop,color):
    if crop.size==0 or min(crop.shape[:2])<16:return crop,False
    hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV);h,s,v=cv2.split(hsv)
    if color=='white':mask=(s<75)&(v>125)
    elif color=='green':mask=(h>=32)&(h<=95)&(s>25)&(v>85)
    elif color=='blue':mask=(h>=85)&(h<=135)&(s>35)&(v>85)
    else:return crop,False
    # Keep separated color components separated. Closing can bridge the label
    # to a similarly colored bay through a one-pixel gap, skewing its rectangle
    # and introducing a false OCR stroke. Holes from digits do not affect the
    # external contour used below.
    mask=mask.astype('uint8')*255
    contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    if not contours:return crop,False
    contour=max(contours,key=cv2.contourArea);rect=cv2.minAreaRect(contour)
    a,b=rect[1]
    if min(a,b)<12 or not .60<a/b<1.67 or a*b<crop.shape[0]*crop.shape[1]*.35:return crop,False
    angle=rect[2]%90
    if angle<12 or angle>78:return crop,False
    # Native resolution and detector clipping limit this geometric aid.
    points=cv2.boxPoints(rect);center=points.mean(0)
    points=points[np.argsort(np.arctan2(points[:,1]-center[1],points[:,0]-center[0]))]
    points=np.roll(points,-int(np.argmin(points.sum(1))),axis=0)
    points=(points-center)*1.06+center
    transform=cv2.getPerspectiveTransform(points.astype('float32'),np.float32([[0,0],[159,0],[159,159],[0,159]]))
    return cv2.warpPerspective(crop,transform,(160,160),borderMode=cv2.BORDER_REPLICATE),True
