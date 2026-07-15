import ChatPanel from "./components/ChatPanel";
import ConfigPanel from "./components/ConfigPanel";
import JobListPanel from "./components/JobListPanel";

export default function App() {
  return (
    <div className="app">
      <ChatPanel />
      <div className="right">
        <ConfigPanel />
        <JobListPanel />
      </div>
    </div>
  );
}
