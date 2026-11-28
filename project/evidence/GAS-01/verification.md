# GAS-01 Hedera estimation verification

Baseline `fca1a7c657b083c3698693b947cd2e788a7bceab`. Public mainnet observations were collected on 2026-11-27 UTC using unsigned Hashio JSON-RPC reads and Hedera Mirror Node GET requests.

## Network evidence

Hashio returned chain ID `0x127` (295). Mirror Node confirmed SaucerSwap V1 RouterV3 at `0.0.3045981` (`0x00000000000000000000000000000000002e7a5d`), with both swap selectors in the deployed runtime. Mirror Node confirmed USDC `0.0.456858` (`0x000000000000000000000000000000000006f89a`, six decimals) and WHBAR `0.0.1456986` (`0x0000000000000000000000000000000000163b5a`, eight decimals). [Official V1 deployments](https://docs.saucerswap.finance/developers/contracts.md) · [Router ABI interface](https://github.com/saucerswaplabs/saucerswap-periphery/blob/master/contracts/interfaces/IUniswapV2Router01.sol)

The estimation calls used the approved sender as both caller and recipient, zero native value, the V1 router as destination, and the ABI signature arguments below. Both calls used path offset 160 bytes, two path addresses and deadline 2026-11-27 16:16:48 UTC. The initial Hashio block was `0x6215164` with hash `0xb4ae17f62303df8d7187571304fa996a0b0fb2c95566ef376a287219a09a0f81`, matching the first 32 bytes of the Mirror Node block hash. The head had advanced to `0x6215165` by 16:16:45 UTC.

| Route | Selector and arguments | Response |
| --- | --- | --- |
| BUY_DEX | `0x8803dbee`, exact output 100,000,000,000 WHBAR base units, maximum input 54,329,049 USDC base units, USDC → WHBAR | HTTP 400, RPC `-32000`, sender account not found |
| SELL_DEX | `0x38ed1739`, exact input 100,000,000,000 WHBAR base units, minimum output 53,437,690 USDC base units, WHBAR → USDC | HTTP 400, RPC `-32000`, sender account not found |

The Mirror Node account, token relationship and token allowance endpoints returned HTTP 404 for the approved EVM address. Hashio returned zero for `eth_getBalance`. Both ERC-20 `balanceOf` calls returned `INVALID_ACCOUNT_ID`, and both router `allowance` calls returned `INVALID_ALLOWANCE_OWNER_ID`. Neither token balance, association nor allowance was established. A BUY needs spendable USDC and a USDC allowance covering the route's input, with the encoded maximum as a conservative prerequisite. A SELL needs at least 1,000 spendable WHBAR and a router allowance for that amount. Both routes also need valid token relationships for spending and receiving.

The sampled `eth_gasPrice` was `0x17233629c00`, or 1,590,000,000,000 EVM wei (weibars) per gas, matching the Mirror Node EthereumTransaction fee schedule of 159 tinybars per gas. The configured 2× cap gives 318 tinybars per gas at that sample. One tinybar is 10¹⁰ weibars and one HBAR is 10⁸ tinybars. Runtime rounds gas units times price up to a whole tinybar and values the result at the fresh fee-free KuCoin ask for the full 1,000 HBAR, rounded up to USDT micro-units under USDC/USDT parity. With neither route returning gas units, this observation supplies no current route gas cost or admission evidence.

## Readiness handoff

Use the existing stable `GasEstimate.estimation_error` codes as typed outcomes. Absence of execution gas maps to `gas_unknown` or `gas_invalid` based on the existing invalid flag. The coordinator also independently rejects stale, expired or mismatched evidence.

| Code | Meaning | Admission outcome |
| --- | --- | --- |
| `estimate_sender_unconfigured` | No sender in the simulation configuration | `gas_unknown` |
| `gas_price_unavailable` | Price transport failure or timeout | `gas_unknown` |
| `gas_price_invalid` | Invalid price response or quantity outside accepted range | `gas_invalid` |
| `route_context_unverified` | Router quote verification unavailable or request deadline already elapsed | `gas_invalid` |
| `route_estimate_unavailable` | Estimation transport failure, timeout, RPC error or rejected call | `gas_unknown` |
| `route_estimate_invalid` | Invalid gas quantity, stale price, elapsed route deadline or invalid provenance | `gas_invalid` |
| `route_estimate_head_changed` | Final RPC head differed or could not be confirmed | `gas_invalid` |

For future readiness diagnostics, keep independently observed sender failures structured. Suggested codes are `sender_account_not_found`, `sender_hbar_balance_insufficient`, `sender_token_balance_insufficient`, `sender_token_association_missing`, `sender_router_allowance_insufficient` and `sender_prerequisites_unverified`. Attach the token and route where applicable. The existing production RPC adapter collapses provider errors into the route-level code above. This mission verified `sender_account_not_found` through independent reads and both simulations.

Require fresh route estimates for the configured sender, a verified router quote, matching route identity, a Mirror Node-correlated snapshot, an unchanged post-estimate head and a current gas-price observation. Re-run both routes after the account identity is resolved. Retain the existing per-route admission and financial rules.
