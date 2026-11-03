import {registerPwa} from './pwa.js';
import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App.jsx';
import './styles/dark.css';
import './styles/themes.css';
import './styles/operations.css';
import './styles/react.css';
createRoot(document.getElementById('root')).render(<React.StrictMode><App/></React.StrictMode>);

registerPwa();
