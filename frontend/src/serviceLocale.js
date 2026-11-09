import translations from './serviceTranslations.js';

const languages = ['ko', 'en', 'zh-CN', 'ja'];

export function getLanguage() {
  if (typeof window === 'undefined' || window.location.pathname.startsWith('/admin/')) return 'ko';
  const query = new URLSearchParams(window.location.search).get('lang');
  if (languages.includes(query)) return query;
  try {
    const saved = localStorage.getItem('parkon-welcome-language');
    return languages.includes(saved) ? saved : 'ko';
  } catch {
    return 'ko';
  }
}

export function t(message, ...values) {
  return translate(getLanguage(), message, ...values);
}

export function translate(language, message, ...values) {
  if (typeof message !== 'string') return message;
  const translation = language === 'ko' ? message : translations[message]?.[languages.indexOf(language) - 1] ?? message;
  return translation.replace(/\{(\d+)\}/g, (match, index) => index < values.length ? String(values[index]) : match);
}
