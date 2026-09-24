// Simple animation for the ad box
AFRAME.registerComponent('rotate-ad', {
  tick: function (time, timeDelta) {
    const rotation = this.el.getAttribute('rotation');
    rotation.y += timeDelta / 1000 * 30; // 30 deg per second
    this.el.setAttribute('rotation', rotation);
  }
});

// Attach the component after scene loads
document.addEventListener('DOMContentLoaded', () => {
  const adBox = document.getElementById('ad-box');
  if (adBox) {
    adBox.setAttribute('rotate-ad', '');
  }
});
