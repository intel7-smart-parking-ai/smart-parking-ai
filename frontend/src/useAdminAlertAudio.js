import {useCallback, useEffect, useRef, useState} from 'react';

const tones = {
 fire: {wave:'triangle', gain:.14, notes:[[0,880,.22],[.26,660,.22],[.52,880,.22],[.78,660,.22]]},
 inquiry: {wave:'sine', gain:.09, notes:[[0,660,.14],[.14,880,.24]]},
};

export function useAdminAlertAudio() {
 const context = useRef(null);
 const enabled = useRef(false);
 const [state, setState] = useState('off');
 useEffect(() => () => {
  enabled.current = false;
  context.current?.close().catch(()=>{});
  context.current = null;
 }, []);

 const play = useCallback(kind => {
  const audio = context.current;
  if (!enabled.current || audio?.state !== 'running') return;
  const {wave,gain,notes} = tones[kind];
  const now = audio.currentTime;
  for (const [offset,frequency,duration] of notes) {
   const start = now + offset;
   const oscillator = audio.createOscillator();
   const volume = audio.createGain();
   oscillator.type = wave;
   oscillator.frequency.value = frequency;
   volume.gain.setValueAtTime(.001,start);
   volume.gain.exponentialRampToValueAtTime(gain,start+.025);
   volume.gain.exponentialRampToValueAtTime(.001,start+duration);
   oscillator.connect(volume).connect(audio.destination);
   oscillator.onended = () => {oscillator.disconnect();volume.disconnect();};
   oscillator.start(start);
   oscillator.stop(start+duration);
  }
 }, []);

 async function toggle() {
  if (enabled.current) {
   enabled.current = false;
   setState('off');
   return;
  }
  try {
   const AudioContext = window.AudioContext || window.webkitAudioContext;
   context.current ??= new AudioContext();
   await context.current.resume();
   enabled.current = true;
   setState('on');
   play('inquiry');
  } catch {
   setState('unavailable');
  }
 }
 return {state,toggle,play};
}
