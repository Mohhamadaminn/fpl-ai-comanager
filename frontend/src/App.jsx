import "./App.css";
import Header from "./components/Header";
import Hero from "./components/Hero";
import TelegramPromo from "./components/TelegramPromo";
import Footer from "./components/Footer";
import ComparePlayers from "./pages/ComparePlayers";

function App() {
  return (
    <div className="site-shell">
      <Header />
      <main>
        <Hero />
        <section id="compare" className="compare-section">
          <ComparePlayers />
        </section>
        <TelegramPromo />
      </main>
      <Footer />
    </div>
  );
}

export default App;