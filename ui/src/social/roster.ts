/** Compatibility shim — prefer ./store and ./types. */
export type { SocialAgent } from "./types";
export { defaultAgents as SOCIAL_ROSTER, agentByName, parseMentions } from "./store";
