import {Component} from 'react';
import {t} from './serviceLocale.js';

export default class ErrorBoundary extends Component {
  state = {failed: false};

  static getDerivedStateFromError() {
    return {failed: true};
  }

  componentDidCatch(error, info) {
    console.error('PARK:ON rendering error', error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;

    return (
      <section className="panel empty-state" role="alert">
        <p>{t('일시적인 화면 오류입니다')}</p>
        <button type="button" className="button primary" onClick={() => window.location.reload()}>
          {t('새로고침')}
        </button>
      </section>
    );
  }
}
