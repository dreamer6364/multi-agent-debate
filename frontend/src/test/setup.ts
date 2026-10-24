// jsdom 缺少 matchMedia / scrollIntoView，AntD 组件依赖它们
window.matchMedia =
  window.matchMedia ||
  ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  }));
Element.prototype.scrollIntoView = Element.prototype.scrollIntoView || (() => undefined);
