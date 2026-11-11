import {useCallback, useEffect, useRef, useState} from 'react';

const tones = {
 fire: {wave:'sawtooth', gain:.22, notes:[[0,650,.65,1250],[.7,1250,.65,650],[1.4,650,.65,1250],[2.1,1250,.65,650],[2.8,650,.65,1250],[3.5,1250,.65,650]]},
 inquiry: {wave:'sine', gain:.09, notes:[[0,660,.14],[.14,880,.24]]},
};

export function useAdminAlertAudio() {
 const context = useRef(null);
 const enabled = useRef(false);
 const fireBuffer = useRef(null);
 const firePlayback = useRef(null);
 const [state, setState] = useState('off');
 useEffect(() => () => {
  enabled.current = false;
  context.current?.close().catch(()=>{});
  context.current = null;
  firePlayback.current = null;
  fireBuffer.current = null;
 }, []);

 const playTone = useCallback(kind => {
  const audio = context.current;
  if (!enabled.current || audio?.state !== 'running') return;
  const {wave,gain,notes} = tones[kind];
  const now = audio.currentTime;
  for (const [offset,frequency,duration,endFrequency] of notes) {
   const start = now + offset;
   const oscillator = audio.createOscillator();
   const volume = audio.createGain();
   oscillator.type = wave;
   oscillator.frequency.setValueAtTime(frequency,start);
   if (endFrequency) oscillator.frequency.linearRampToValueAtTime(endFrequency,start+duration);
   volume.gain.setValueAtTime(.001,start);
   volume.gain.exponentialRampToValueAtTime(gain,start+.025);
   volume.gain.exponentialRampToValueAtTime(.001,start+duration);
   oscillator.connect(volume).connect(audio.destination);
   oscillator.onended = () => {oscillator.disconnect();volume.disconnect();};
   oscillator.start(start);
   oscillator.stop(start+duration);
  }
 }, []);

 const play = useCallback(async kind => {
  if (kind !== 'fire') return playTone(kind);
  const audio = context.current;
  if (!enabled.current || audio?.state !== 'running' || firePlayback.current) return;

  const playback = {source: null};
  firePlayback.current = playback;
  try {
   fireBuffer.current ??= fetch('/fire-alarm.mp3').then(response => {
    if (!response.ok) throw new Error('Fire alarm audio unavailable');
    return response.arrayBuffer();
   }).then(bytes => audio.decodeAudioData(bytes));
   const buffer = await fireBuffer.current;
   if (firePlayback.current !== playback) return;
   const source = audio.createBufferSource();
   playback.source = source;
   source.buffer = buffer;
   source.connect(audio.destination);
   source.onended = () => {
    source.disconnect();
    if (firePlayback.current === playback) firePlayback.current = null;
   };
   source.start();
  } catch {
   fireBuffer.current = null;
   if (firePlayback.current !== playback) return;
   firePlayback.current = null;
   playTone('fire');
  }
 }, [playTone]);

 async function toggle() {
  if (enabled.current) {
   enabled.current = false;
   firePlayback.current?.source?.stop();
   firePlayback.current = null;
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
