(function(){
  const reader = document.getElementById('reader');
  const status = document.getElementById('scan-status');
  const appOrigin = document.body.dataset.appOrigin || window.location.origin;
  const appPathPrefix = document.body.dataset.appPathPrefix || '/';

  function setStatus(message, level='secondary') {
    if (!status) return;
    status.className = 'alert alert-' + level;
    status.textContent = message;
  }

  function normalizeDecodedTarget(decodedText) {
    const raw = (decodedText || '').trim();
    if (!raw) return null;
    try {
      const url = new URL(raw, window.location.origin);
      if (!['http:', 'https:'].includes(url.protocol)) return null;
      if (url.origin !== appOrigin) return null;
      if (!url.pathname.startsWith(appPathPrefix)) return null;
      return url.pathname + url.search + url.hash;
    } catch (e) {
      return null;
    }
  }

  async function start() {
    if (!reader) return;
    if (!('BarcodeDetector' in window)) {
      setStatus('Ta przeglądarka nie wspiera skanowania QR przez BarcodeDetector. Użyj nowszego Chrome/Edge na telefonie.', 'warning');
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
      const video = document.createElement('video');
      video.setAttribute('playsinline', 'true');
      video.autoplay = true;
      video.srcObject = stream;
      reader.innerHTML = '';
      reader.appendChild(video);
      await video.play();
      const detector = new BarcodeDetector({ formats: ['qr_code'] });
      const scan = async () => {
        try {
          const codes = await detector.detect(video);
          if (codes && codes.length > 0) {
            const target = normalizeDecodedTarget(codes[0].rawValue);
            if (target) {
              setStatus('Kod rozpoznany. Trwa otwieranie…', 'success');
              stream.getTracks().forEach(t => t.stop());
              window.location.assign(target);
              return;
            }
            setStatus('Kod QR został odczytany, ale nie wskazuje na prawidłowy adres tej aplikacji.', 'danger');
          }
        } catch (err) {
          setStatus('Błąd podczas skanowania: ' + err.message, 'danger');
          stream.getTracks().forEach(t => t.stop());
          return;
        }
        requestAnimationFrame(scan);
      };
      setStatus('Skieruj kamerę na kod QR urządzenia.', 'info');
      requestAnimationFrame(scan);
    } catch (err) {
      setStatus('Nie udało się uruchomić kamery: ' + err.message, 'danger');
      if (stream) stream.getTracks().forEach(t => t.stop());
    }
  }

  document.addEventListener('DOMContentLoaded', start);
})();
